#pragma once
#include <rex/input/input_system.h>
std::unique_ptr<rex::system::IInputSystem> CreateControllerInputSystem(bool tool_mode);
