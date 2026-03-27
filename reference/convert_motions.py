#!/usr/bin/env python3
"""
Convert supported G1 motion pickle formats into SONIC reference-motion folders.

Supported inputs:
1. Full-kinematics motion pickles:
   - Multi-motion dict: {motion_name: {...}}
   - Single-motion dict: {"joint_pos", "joint_vel", "body_pos_w", ...}
2. ScaleLab / GMR single-motion pickles:
   - {"fps", "root_pos", "root_rot", "dof_pos", ...}

The output matches the C++ deployment reader format documented in
`docs/source/references/motion_reference.md`.
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np


DEFAULT_TARGET_FPS = 50.0
SCALELAB_REQUIRED_KEYS = {"fps", "root_pos", "root_rot", "dof_pos"}
FULL_MOTION_REQUIRED_KEYS = {"joint_pos", "joint_vel", "body_pos_w", "body_quat_w"}

# SONIC's reference-motion reader stores joint trajectories in IsaacLab order.
# GMR / ScaleLab pickles store `dof_pos` as MuJoCo qpos[7:] order.
# We therefore need to remap MuJoCo-ordered joints -> IsaacLab-ordered joints
# before writing `joint_pos.csv`, otherwise arm joints are interpreted as the
# wrong shoulder / elbow / wrist channels during visualization and playback.
ISAACLAB_INDEX_TO_MUJOCO_INDEX = np.array(
    [
        0,
        6,
        12,
        1,
        7,
        13,
        2,
        8,
        14,
        3,
        9,
        15,
        22,
        4,
        10,
        16,
        23,
        5,
        11,
        17,
        24,
        18,
        25,
        19,
        26,
        20,
        27,
        21,
        28,
    ],
    dtype=np.int64,
)

DEFAULT_SCALELAB_ROOT_HEADING_MODE = "follow"
DEFAULT_JOINT_VELOCITY_SMOOTHING_WINDOW = 1


def load_pickle(path: Path):
    """Load a pickle file with joblib when available, then fall back to pickle."""
    try:
        import joblib

        data = joblib.load(path)
        print(f"✓ Loaded {path.name} with joblib")
        return data
    except ImportError:
        pass
    except Exception as exc:
        print(f"⚠ joblib load failed for {path.name}: {exc}")

    with path.open("rb") as handle:
        data = pickle.load(handle)
    print(f"✓ Loaded {path.name} with pickle")
    return data


def is_scalelab_motion_dict(data) -> bool:
    return isinstance(data, dict) and SCALELAB_REQUIRED_KEYS.issubset(data.keys())


def is_full_motion_dict(data) -> bool:
    return isinstance(data, dict) and FULL_MOTION_REQUIRED_KEYS.issubset(data.keys())


def extract_motion_items(data, fallback_name: str) -> tuple[str, dict[str, dict]]:
    """Normalize supported pickle layouts to {motion_name: motion_dict}."""
    if is_scalelab_motion_dict(data):
        return "scalelab_gmr", {fallback_name: data}

    if is_full_motion_dict(data):
        return "full_kinematics_single", {fallback_name: data}

    if isinstance(data, dict) and data:
        values = list(data.values())
        if all(isinstance(v, dict) for v in values):
            if all(is_scalelab_motion_dict(v) for v in values):
                return "scalelab_gmr_multi", {str(k): v for k, v in data.items()}
            if all(is_full_motion_dict(v) for v in values):
                return "full_kinematics_multi", {str(k): v for k, v in data.items()}

    raise ValueError(
        "Unsupported pickle format. Expected either full-kinematics motion data "
        "or ScaleLab/GMR data with fps/root_pos/root_rot/dof_pos."
    )


def ensure_2d_array(name: str, value, expected_last_dim: int) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != expected_last_dim:
        raise ValueError(f"{name} must have shape [T, {expected_last_dim}], got {array.shape}")
    return array


def ensure_3d_array(name: str, value, expected_last_dim: int) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.ndim != 3 or array.shape[2] != expected_last_dim:
        raise ValueError(
            f"{name} must have shape [T, B, {expected_last_dim}], got {array.shape}"
        )
    return array


def normalize_quaternions_xyzw(quaternions: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(quaternions, axis=1, keepdims=True)
    if np.any(norms < 1e-8):
        raise ValueError("Encountered near-zero quaternion norm")
    normalized = quaternions / norms
    # Flip signs for continuity so slerp does not take the long path.
    for idx in range(1, normalized.shape[0]):
        if np.dot(normalized[idx - 1], normalized[idx]) < 0.0:
            normalized[idx] *= -1.0
    return normalized


def xyzw_to_wxyz(quaternions_xyzw: np.ndarray) -> np.ndarray:
    return quaternions_xyzw[:, [3, 0, 1, 2]]


def resample_linear(data: np.ndarray, source_fps: float, target_fps: float) -> np.ndarray:
    if source_fps <= 0.0:
        raise ValueError(f"source_fps must be positive, got {source_fps}")
    if target_fps <= 0.0:
        raise ValueError(f"target_fps must be positive, got {target_fps}")
    if data.shape[0] == 0:
        raise ValueError("Cannot resample empty data")
    if data.shape[0] == 1 or abs(source_fps - target_fps) < 1e-9:
        return data.copy()

    duration = (data.shape[0] - 1) / source_fps
    target_times = np.arange(0.0, duration + 1e-6, 1.0 / target_fps, dtype=np.float64)
    frame_indices = target_times * source_fps

    idx0 = np.floor(frame_indices).astype(np.int64)
    idx1 = np.clip(idx0 + 1, 0, data.shape[0] - 1)
    alpha = (frame_indices - idx0).reshape(-1, *([1] * (data.ndim - 1)))

    return (1.0 - alpha) * data[idx0] + alpha * data[idx1]


def slerp_pair_xyzw(q0: np.ndarray, q1: np.ndarray, alpha: float) -> np.ndarray:
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot

    dot = np.clip(dot, -1.0, 1.0)
    if dot > 0.9995:
        blended = q0 + alpha * (q1 - q0)
        return blended / np.linalg.norm(blended)

    theta_0 = np.arccos(dot)
    sin_theta_0 = np.sin(theta_0)
    theta = theta_0 * alpha
    sin_theta = np.sin(theta)

    s0 = np.sin(theta_0 - theta) / sin_theta_0
    s1 = sin_theta / sin_theta_0
    return s0 * q0 + s1 * q1


def resample_quaternions_xyzw(quaternions_xyzw: np.ndarray, source_fps: float, target_fps: float) -> np.ndarray:
    quaternions_xyzw = normalize_quaternions_xyzw(quaternions_xyzw)
    if quaternions_xyzw.shape[0] == 1 or abs(source_fps - target_fps) < 1e-9:
        return quaternions_xyzw.copy()

    duration = (quaternions_xyzw.shape[0] - 1) / source_fps
    target_times = np.arange(0.0, duration + 1e-6, 1.0 / target_fps, dtype=np.float64)
    frame_indices = target_times * source_fps

    out = np.empty((len(frame_indices), 4), dtype=np.float64)
    for out_idx, frame_idx in enumerate(frame_indices):
        idx0 = int(np.floor(frame_idx))
        idx1 = min(idx0 + 1, quaternions_xyzw.shape[0] - 1)
        alpha = float(frame_idx - idx0)
        if idx0 == idx1:
            out[out_idx] = quaternions_xyzw[idx0]
        else:
            out[out_idx] = slerp_pair_xyzw(quaternions_xyzw[idx0], quaternions_xyzw[idx1], alpha)
    return normalize_quaternions_xyzw(out)


def finite_difference(data: np.ndarray, fps: float) -> np.ndarray:
    if data.shape[0] <= 1:
        return np.zeros_like(data)
    edge_order = 2 if data.shape[0] > 2 else 1
    return np.gradient(data, 1.0 / fps, axis=0, edge_order=edge_order)


def smooth_time_series(data: np.ndarray, window_size: int) -> np.ndarray:
    if window_size <= 1 or data.shape[0] <= 1:
        return np.asarray(data, dtype=np.float64).copy()
    if window_size % 2 == 0:
        raise ValueError(f"window_size must be odd, got {window_size}")

    flat = np.asarray(data, dtype=np.float64).reshape(data.shape[0], -1)
    pad = window_size // 2
    padded = np.pad(flat, ((pad, pad), (0, 0)), mode="edge")
    out = np.empty_like(flat)
    for frame_idx in range(flat.shape[0]):
        out[frame_idx] = padded[frame_idx : frame_idx + window_size].mean(axis=0)
    return out.reshape(data.shape)


def quat_conjugate_wxyz(quat: np.ndarray) -> np.ndarray:
    return np.array([quat[0], -quat[1], -quat[2], -quat[3]], dtype=np.float64)


def quat_multiply_wxyz(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = q1
    w2, x2, y2, z2 = q2
    return np.array(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ],
        dtype=np.float64,
    )


def normalize_quaternions_wxyz(quaternions_wxyz: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(quaternions_wxyz, axis=1, keepdims=True)
    if np.any(norms < 1e-8):
        raise ValueError("Encountered near-zero quaternion norm")
    normalized = quaternions_wxyz / norms
    for idx in range(1, normalized.shape[0]):
        if np.dot(normalized[idx - 1], normalized[idx]) < 0.0:
            normalized[idx] *= -1.0
    return normalized


def yaw_quaternion_wxyz(quat_wxyz: np.ndarray) -> np.ndarray:
    w, x, y, z = quat_wxyz
    yaw = np.arctan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return np.array([np.cos(0.5 * yaw), 0.0, 0.0, np.sin(0.5 * yaw)], dtype=np.float64)


def stabilize_root_heading_wxyz(
    quaternions_wxyz: np.ndarray,
    heading_mode: str,
) -> np.ndarray:
    quaternions_wxyz = normalize_quaternions_wxyz(quaternions_wxyz)
    if heading_mode == "follow":
        return quaternions_wxyz
    if heading_mode != "lock_initial":
        raise ValueError(f"Unsupported heading_mode: {heading_mode}")

    initial_heading = yaw_quaternion_wxyz(quaternions_wxyz[0])
    stabilized = np.empty_like(quaternions_wxyz)
    for idx, quat in enumerate(quaternions_wxyz):
        heading_inv = quat_conjugate_wxyz(yaw_quaternion_wxyz(quat))
        tilt_only = quat_multiply_wxyz(heading_inv, quat)
        stabilized[idx] = quat_multiply_wxyz(initial_heading, tilt_only)
    return normalize_quaternions_wxyz(stabilized)


def quaternion_angular_velocity_wxyz(quaternions_wxyz: np.ndarray, fps: float) -> np.ndarray:
    if quaternions_wxyz.shape[0] <= 1:
        return np.zeros((quaternions_wxyz.shape[0], 3), dtype=np.float64)

    ang_vel = np.zeros((quaternions_wxyz.shape[0], 3), dtype=np.float64)
    for idx in range(quaternions_wxyz.shape[0]):
        prev_idx = max(idx - 1, 0)
        next_idx = min(idx + 1, quaternions_wxyz.shape[0] - 1)
        dt = max(1, next_idx - prev_idx) / fps
        dq = quat_multiply_wxyz(
            quaternions_wxyz[next_idx], quat_conjugate_wxyz(quaternions_wxyz[prev_idx])
        )
        dq_norm = np.linalg.norm(dq)
        if dq_norm < 1e-8:
            continue
        dq = dq / dq_norm

        angle = 2.0 * np.arctan2(np.linalg.norm(dq[1:]), abs(dq[0]))
        if angle < 1e-8:
            continue
        axis_norm = np.linalg.norm(dq[1:])
        if axis_norm < 1e-8:
            continue
        axis = dq[1:] / axis_norm
        if dq[0] < 0.0:
            axis = -axis
        ang_vel[idx] = axis * (angle / dt)
    return ang_vel


def convert_scalelab_motion(
    motion_name: str,
    motion_data: dict,
    target_fps: float,
    joint_velocity_smoothing_window: int = DEFAULT_JOINT_VELOCITY_SMOOTHING_WINDOW,
    root_heading_mode: str = DEFAULT_SCALELAB_ROOT_HEADING_MODE,
) -> dict:
    source_fps = float(motion_data["fps"])
    root_pos = ensure_2d_array("root_pos", motion_data["root_pos"], 3)
    root_rot_xyzw = ensure_2d_array("root_rot", motion_data["root_rot"], 4)
    dof_pos_mujoco = ensure_2d_array("dof_pos", motion_data["dof_pos"], 29)

    if not (root_pos.shape[0] == root_rot_xyzw.shape[0] == dof_pos_mujoco.shape[0]):
        raise ValueError(
            f"Frame count mismatch for {motion_name}: "
            f"root_pos={root_pos.shape[0]}, root_rot={root_rot_xyzw.shape[0]}, dof_pos={dof_pos_mujoco.shape[0]}"
        )

    joint_pos_mujoco = resample_linear(dof_pos_mujoco, source_fps, target_fps)
    joint_pos = joint_pos_mujoco[:, ISAACLAB_INDEX_TO_MUJOCO_INDEX]
    root_pos_resampled = resample_linear(root_pos, source_fps, target_fps)
    root_rot_resampled_xyzw = resample_quaternions_xyzw(root_rot_xyzw, source_fps, target_fps)
    root_rot_resampled_wxyz = stabilize_root_heading_wxyz(
        xyzw_to_wxyz(root_rot_resampled_xyzw),
        root_heading_mode,
    )

    joint_vel_source = smooth_time_series(joint_pos, joint_velocity_smoothing_window)
    joint_vel = finite_difference(joint_vel_source, target_fps)
    body_lin_vel = finite_difference(root_pos_resampled, target_fps)
    body_ang_vel = quaternion_angular_velocity_wxyz(root_rot_resampled_wxyz, target_fps)

    return {
        "joint_pos": joint_pos,
        "joint_vel": joint_vel,
        "body_pos_w": root_pos_resampled[:, None, :],
        "body_quat_w": root_rot_resampled_wxyz[:, None, :],
        "body_lin_vel_w": body_lin_vel[:, None, :],
        "body_ang_vel_w": body_ang_vel[:, None, :],
        "_body_indexes": np.array([0], dtype=np.int64),
        "time_step_total": int(joint_pos.shape[0]),
        "_source_format": "scalelab_gmr",
        "_source_fps": source_fps,
        "_target_fps": float(target_fps),
        "_root_rotation_input_order": "xyzw",
        "_output_body_quaternion_order": "wxyz",
        "_source_joint_order": "mujoco_qpos",
        "_output_joint_order": "isaaclab",
        "_root_heading_mode": root_heading_mode,
        "_joint_velocity_smoothing_window": int(joint_velocity_smoothing_window),
    }


def normalize_full_kinematics_motion(motion_name: str, motion_data: dict) -> dict:
    joint_pos = ensure_2d_array("joint_pos", motion_data["joint_pos"], 29)
    joint_vel = ensure_2d_array("joint_vel", motion_data["joint_vel"], 29)
    body_pos_w = ensure_3d_array("body_pos_w", motion_data["body_pos_w"], 3)
    body_quat_w = ensure_3d_array("body_quat_w", motion_data["body_quat_w"], 4)
    body_lin_vel_w = ensure_3d_array("body_lin_vel_w", motion_data["body_lin_vel_w"], 3)
    body_ang_vel_w = ensure_3d_array("body_ang_vel_w", motion_data["body_ang_vel_w"], 3)

    frame_count = joint_pos.shape[0]
    expected_frames = [
        joint_vel.shape[0],
        body_pos_w.shape[0],
        body_quat_w.shape[0],
        body_lin_vel_w.shape[0],
        body_ang_vel_w.shape[0],
    ]
    if any(count != frame_count for count in expected_frames):
        raise ValueError(f"Frame count mismatch in full-kinematics motion {motion_name}")

    body_indexes = motion_data.get("_body_indexes")
    if body_indexes is None:
        body_indexes = np.arange(body_pos_w.shape[1], dtype=np.int64)
    body_indexes = np.asarray(body_indexes, dtype=np.int64).reshape(-1)

    return {
        "joint_pos": joint_pos,
        "joint_vel": joint_vel,
        "body_pos_w": body_pos_w,
        "body_quat_w": body_quat_w,
        "body_lin_vel_w": body_lin_vel_w,
        "body_ang_vel_w": body_ang_vel_w,
        "_body_indexes": body_indexes,
        "time_step_total": int(motion_data.get("time_step_total", frame_count)),
        "_source_format": motion_data.get("_source_format", "full_kinematics"),
        "_source_fps": motion_data.get("_source_fps", "unknown"),
        "_target_fps": motion_data.get("_target_fps", "unknown"),
    }


def convert_motion_dict(
    motion_name: str,
    motion_data: dict,
    target_fps: float,
    joint_velocity_smoothing_window: int = DEFAULT_JOINT_VELOCITY_SMOOTHING_WINDOW,
    root_heading_mode: str = DEFAULT_SCALELAB_ROOT_HEADING_MODE,
) -> dict:
    if is_scalelab_motion_dict(motion_data):
        return convert_scalelab_motion(
            motion_name,
            motion_data,
            target_fps,
            joint_velocity_smoothing_window=joint_velocity_smoothing_window,
            root_heading_mode=root_heading_mode,
        )
    if is_full_motion_dict(motion_data):
        return normalize_full_kinematics_motion(motion_name, motion_data)
    raise ValueError(f"Unsupported motion layout for {motion_name}")


def save_array_as_csv(array: np.ndarray, filename: Path, headers: list[str]) -> None:
    array = np.asarray(array, dtype=np.float64)
    if array.ndim != 2:
        raise ValueError(f"Expected 2D array for {filename}, got shape {array.shape}")
    with filename.open("w", encoding="utf-8") as handle:
        handle.write(",".join(headers) + "\n")
        for row in array:
            handle.write(",".join(f"{value:.6f}" for value in row) + "\n")


def save_motion_files(motion_name: str, motion_data: dict, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    joint_pos = np.asarray(motion_data["joint_pos"], dtype=np.float64)
    joint_vel = np.asarray(motion_data["joint_vel"], dtype=np.float64)
    body_pos_w = np.asarray(motion_data["body_pos_w"], dtype=np.float64)
    body_quat_w = np.asarray(motion_data["body_quat_w"], dtype=np.float64)
    body_lin_vel_w = np.asarray(motion_data["body_lin_vel_w"], dtype=np.float64)
    body_ang_vel_w = np.asarray(motion_data["body_ang_vel_w"], dtype=np.float64)
    body_indexes = np.asarray(motion_data["_body_indexes"], dtype=np.int64).reshape(-1)

    timesteps = joint_pos.shape[0]
    num_joints = joint_pos.shape[1]
    num_bodies = body_pos_w.shape[1]

    save_array_as_csv(
        joint_pos,
        output_dir / "joint_pos.csv",
        [f"joint_{idx}" for idx in range(num_joints)],
    )
    save_array_as_csv(
        joint_vel,
        output_dir / "joint_vel.csv",
        [f"joint_vel_{idx}" for idx in range(num_joints)],
    )

    body_pos_flat = body_pos_w.reshape(timesteps, -1)
    body_quat_flat = body_quat_w.reshape(timesteps, -1)
    body_lin_vel_flat = body_lin_vel_w.reshape(timesteps, -1)
    body_ang_vel_flat = body_ang_vel_w.reshape(timesteps, -1)

    save_array_as_csv(
        body_pos_flat,
        output_dir / "body_pos.csv",
        [f"body_{body}_{axis}" for body in range(num_bodies) for axis in "xyz"],
    )
    save_array_as_csv(
        body_quat_flat,
        output_dir / "body_quat.csv",
        [f"body_{body}_{axis}" for body in range(body_quat_w.shape[1]) for axis in "wxyz"],
    )
    save_array_as_csv(
        body_lin_vel_flat,
        output_dir / "body_lin_vel.csv",
        [f"body_{body}_vel_{axis}" for body in range(num_bodies) for axis in "xyz"],
    )
    save_array_as_csv(
        body_ang_vel_flat,
        output_dir / "body_ang_vel.csv",
        [f"body_{body}_angvel_{axis}" for body in range(num_bodies) for axis in "xyz"],
    )

    save_metadata(motion_name, motion_data, output_dir / "metadata.txt")
    save_motion_info(motion_name, motion_data, output_dir / "info.txt")

    source_fps = motion_data.get("_source_fps", "unknown")
    target_fps = motion_data.get("_target_fps", "unknown")

    return {
        "motion_name": motion_name,
        "output_dir": str(output_dir),
        "timesteps": timesteps,
        "joints": num_joints,
        "body_parts": num_bodies,
        "source_format": motion_data.get("_source_format", "unknown"),
        "source_fps": source_fps,
        "target_fps": target_fps,
        "body_indexes": body_indexes.tolist(),
    }


def save_metadata(motion_name: str, motion_data: dict, filename: Path) -> None:
    body_indexes = np.asarray(motion_data["_body_indexes"], dtype=np.int64).reshape(-1)
    total_timesteps = int(motion_data["time_step_total"])

    with filename.open("w", encoding="utf-8") as handle:
        handle.write(f"Metadata for: {motion_name}\n")
        handle.write("=" * 30 + "\n\n")
        handle.write("Body part indexes:\n")
        handle.write("[" + " ".join(str(idx) for idx in body_indexes) + "]\n\n")
        handle.write(f"Total timesteps: {total_timesteps}\n\n")
        handle.write("Data arrays summary:\n")
        for key in (
            "joint_pos",
            "joint_vel",
            "body_pos_w",
            "body_quat_w",
            "body_lin_vel_w",
            "body_ang_vel_w",
        ):
            value = np.asarray(motion_data[key])
            handle.write(f"  {key}: {value.shape} ({value.dtype})\n")


def save_motion_info(motion_name: str, motion_data: dict, filename: Path) -> None:
    with filename.open("w", encoding="utf-8") as handle:
        handle.write(f"Motion Information: {motion_name}\n")
        handle.write("=" * 50 + "\n\n")

        for meta_key in (
            "_source_format",
            "_source_fps",
            "_target_fps",
            "_root_rotation_input_order",
            "_output_body_quaternion_order",
            "_source_joint_order",
            "_output_joint_order",
            "_root_heading_mode",
            "_joint_velocity_smoothing_window",
        ):
            if meta_key in motion_data:
                handle.write(f"{meta_key}:\n")
                handle.write(f"  Value: {motion_data[meta_key]}\n\n")

        for key in (
            "joint_pos",
            "joint_vel",
            "body_pos_w",
            "body_quat_w",
            "body_lin_vel_w",
            "body_ang_vel_w",
        ):
            value = np.asarray(motion_data[key], dtype=np.float64)
            flat_vals = value.reshape(-1)
            handle.write(f"{key}:\n")
            handle.write(f"  Shape: {value.shape}\n")
            handle.write(f"  Dtype: {value.dtype}\n")
            handle.write(f"  Range: [{flat_vals.min():.6f}, {flat_vals.max():.6f}]\n")
            handle.write(f"  Sample: {flat_vals[:5]}\n\n")

        body_indexes = np.asarray(motion_data["_body_indexes"], dtype=np.int64).reshape(-1)
        handle.write("_body_indexes:\n")
        handle.write(f"  Value: {body_indexes.tolist()}\n\n")

        handle.write("time_step_total:\n")
        handle.write(f"  Value: {int(motion_data['time_step_total'])}\n")


def create_summary_file(records: list[dict], output_dir: Path) -> None:
    if not records:
        return

    summary_file = output_dir / "motion_summary.txt"
    with summary_file.open("w", encoding="utf-8") as handle:
        handle.write("G1 Motion Capture Data Summary\n")
        handle.write("=" * 40 + "\n\n")
        handle.write(f"Total motion sequences: {len(records)}\n\n")
        handle.write("Detailed motion list:\n")
        for record in records:
            handle.write(f"  {record['motion_name']}:\n")
            handle.write(f"    Output dir: {record['output_dir']}\n")
            handle.write(f"    Timesteps: {record['timesteps']}\n")
            handle.write(f"    Joints: {record['joints']}\n")
            handle.write(f"    Body parts: {record['body_parts']}\n")
            handle.write(f"    Source format: {record['source_format']}\n")
            handle.write(f"    Source FPS: {record['source_fps']}\n")
            handle.write(f"    Target FPS: {record['target_fps']}\n")
            handle.write(f"    Body indexes: {record['body_indexes']}\n\n")


def resolve_output_root(
    pickle_path: Path,
    explicit_output_dir: Path | None,
    motion_count: int,
) -> Path:
    if explicit_output_dir is not None:
        return explicit_output_dir
    if motion_count > 1:
        return pickle_path.parent / pickle_path.stem
    return pickle_path.parent


def convert_pickle_file(
    pickle_path: Path,
    explicit_output_dir: Path | None,
    target_fps: float,
    joint_velocity_smoothing_window: int,
    root_heading_mode: str,
) -> tuple[Path, list[dict]]:
    print(f"\nConverting motion data from: {pickle_path}")
    raw_data = load_pickle(pickle_path)
    source_layout, motions = extract_motion_items(raw_data, pickle_path.stem)

    output_root = resolve_output_root(pickle_path, explicit_output_dir, len(motions))
    output_root.mkdir(parents=True, exist_ok=True)
    print(f"Output directory structure: {output_root}/")
    print(f"Detected layout: {source_layout}")
    print(f"Found {len(motions)} motion sequence(s):")
    for motion_name in motions:
        print(f"  - {motion_name}")

    records = []
    success_count = 0
    for motion_name, motion_dict in motions.items():
        print(f"\nProcessing: {motion_name}")
        motion_output_dir = output_root / motion_name
        print(f"Creating motion folder: {motion_output_dir}")
        converted_motion = convert_motion_dict(
            motion_name,
            motion_dict,
            target_fps,
            joint_velocity_smoothing_window=joint_velocity_smoothing_window,
            root_heading_mode=root_heading_mode,
        )
        record = save_motion_files(motion_name, converted_motion, motion_output_dir)
        records.append(record)
        success_count += 1
        print(
            f"  ✓ Saved {record['timesteps']} frames, {record['joints']} joints, "
            f"{record['body_parts']} body part(s)"
        )

    print(f"\n✓ Successfully converted {success_count}/{len(motions)} motions")
    print(f"Output files saved to: {output_root}/")
    return output_root, records


def iter_pickle_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(path for path in input_path.iterdir() if path.suffix == ".pkl")
    raise FileNotFoundError(f"Input not found: {input_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert motion pickle files into SONIC reference-motion CSV folders."
    )
    parser.add_argument(
        "input_path",
        help="A .pkl file or a directory containing .pkl files",
    )
    parser.add_argument(
        "output_base_dir",
        nargs="?",
        default=None,
        help="Optional output base directory. Defaults to a path near the input.",
    )
    parser.add_argument(
        "--target-fps",
        type=float,
        default=DEFAULT_TARGET_FPS,
        help=f"Target SONIC playback FPS for ScaleLab/GMR inputs (default: {DEFAULT_TARGET_FPS})",
    )
    parser.add_argument(
        "--joint-velocity-smoothing-window",
        type=int,
        default=DEFAULT_JOINT_VELOCITY_SMOOTHING_WINDOW,
        help=(
            "Odd moving-average window applied to ScaleLab joint positions before "
            "computing joint_vel.csv (default: 1 = disabled)"
        ),
    )
    parser.add_argument(
        "--root-heading-mode",
        choices=("follow", "lock_initial"),
        default=DEFAULT_SCALELAB_ROOT_HEADING_MODE,
        help=(
            "How to treat ScaleLab root heading when generating body_quat.csv. "
            "'follow' keeps the source heading, 'lock_initial' removes heading drift "
            "while preserving tilt (default: follow)"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_path).expanduser().resolve()
    output_base_dir = (
        Path(args.output_base_dir).expanduser().resolve() if args.output_base_dir else None
    )

    if not input_path.exists():
        print(f"Error: Input not found: {input_path}")
        return 1

    pickle_paths = iter_pickle_inputs(input_path)
    if not pickle_paths:
        print(f"Error: No .pkl files found in {input_path}")
        return 1
    if args.joint_velocity_smoothing_window < 1:
        print("Error: --joint-velocity-smoothing-window must be >= 1")
        return 1
    if args.joint_velocity_smoothing_window % 2 == 0:
        print("Error: --joint-velocity-smoothing-window must be odd")
        return 1

    print("G1 Motion Data Converter")
    print("========================")

    summary_by_root: dict[Path, list[dict]] = {}
    total_records = []

    for pickle_path in pickle_paths:
        output_root, records = convert_pickle_file(
            pickle_path=pickle_path,
            explicit_output_dir=output_base_dir,
            target_fps=args.target_fps,
            joint_velocity_smoothing_window=args.joint_velocity_smoothing_window,
            root_heading_mode=args.root_heading_mode,
        )
        summary_by_root.setdefault(output_root, []).extend(records)
        total_records.extend(records)

    for output_root, records in summary_by_root.items():
        create_summary_file(records, output_root)

    print("\n✓ Conversion completed successfully!")
    print(f"Converted {len(total_records)} motion(s) across {len(summary_by_root)} output folder(s).")
    print("\nNext steps:")
    print("1. Visualize a motion:")
    print("   python visualize_motion.py --motion_dir <motion_folder>")
    print("2. Launch SONIC with the parent motion directory:")
    print("   ./deploy.sh --motion-data <motion_dataset_dir> sim")
    print("3. During runtime use T / N / P / R to play, switch, and restart motions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
