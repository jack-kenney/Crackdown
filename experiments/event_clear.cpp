// Synchronize the profiled worker's inlined KeClearEvent with SDK host events.
#include "generated/crackdown_pch.h"
#include <rex/cvar.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xevent.h>

REXCVAR_DEFINE_BOOL(fix_guest_event_clear, false, "Experiments",
    "Clear native events when the profiled guest worker clears its dispatcher headers; restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace {
constexpr bool IsWorkerClearCaller(uint32_t caller) {
    return caller == 0x8225C618 || caller == 0x8225C6B8 ||
           caller == 0x8225C6C4 || caller == 0x8225C74C;
}

void ClearInitializedEvent(uint32_t address, uint8_t* base) {
    // A guest-only clear before first use already controls initial native state.
    // Avoid initializing unrelated objects or interpreting non-event headers.
    if (!address || REX_LOAD_U8(address) > 1 ||
        REX_LOAD_U32(address + 8) != rex::system::kXObjSignature) return;
#ifdef CRACKDOWN_EVENT_CLEAR_TEST
    auto event = TestResolveEvent(address, base);
#else
    auto* kernel = REX_KERNEL_STATE();
    if (!kernel) return;
    auto event = rex::system::XObject::GetNativeObject<rex::system::XEvent>(
        kernel, base + address);
#endif
    if (event && event->type() == rex::system::XObject::Type::Event) event->Clear();
}
}  // namespace

REX_EXTERN(__imp__sub_82A4FA70);
REX_EXTERN(sub_82A4FA70) {
    // Requires-restart metadata still lets the SDK change backing flag storage.
    // Switching to guest-only clears after native signals have been consumed can
    // reintroduce stale completion events into an already advanced stream queue.
    // Keep BOTH cold-start modes consistent throughout this process lifetime.
    static const bool enabled = REXCVAR_GET(fix_guest_event_clear);
    const uint32_t caller = uint32_t(ctx.lr);
    const uint32_t address = ctx.r3.u32;
    const bool synchronize = enabled && IsWorkerClearCaller(caller);
    __imp__sub_82A4FA70(ctx, base);
    if (synchronize) ClearInitializedEvent(address, base);
    // The original guest store, return value and register effects are retained.
}
