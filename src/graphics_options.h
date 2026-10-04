#pragma once
#include <cstdint>

namespace rex::graphics { class GraphicsSystem; }
namespace rex::ui { class Window; }

void ApplyGraphicsOptions(rex::graphics::GraphicsSystem* graphics, uint8_t* membase);
void ApplyLaunchWindowOptions(rex::ui::Window* window);
