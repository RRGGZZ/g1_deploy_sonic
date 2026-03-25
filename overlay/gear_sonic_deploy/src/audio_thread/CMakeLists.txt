add_library(audio_thread audio_thread.cpp)
target_include_directories(audio_thread PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(audio_thread PRIVATE unitree_sdk2)

add_executable(g1_audio_debug_player play_audio_debug.cpp)
target_include_directories(g1_audio_debug_player PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(g1_audio_debug_player PRIVATE unitree_sdk2)
set_target_properties(
  g1_audio_debug_player
  PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${PROJECT_SOURCE_DIR}/target/release/"
             OUTPUT_NAME g1_audio_debug_player
             CXX_STANDARD 20
             CXX_STANDARD_REQUIRED ON
             CXX_EXTENSIONS OFF)
