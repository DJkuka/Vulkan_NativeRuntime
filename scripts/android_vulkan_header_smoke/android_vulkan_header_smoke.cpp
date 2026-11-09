#include <vulkan/vulkan.hpp>
#include <spirv/unified1/spirv.hpp>

static_assert(VK_HEADER_VERSION > 0, "Vulkan-Headers version macro is unavailable");
static_assert(spv::MagicNumber == 0x07230203, "SPIR-V magic number mismatch");

int android_vulkan_header_smoke() {
    return static_cast<int>(VK_HEADER_VERSION);
}
