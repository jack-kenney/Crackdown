#include "generated/crackdown_pch.h"
#include <rex/cvar.h>
#include <rex/system/xevent.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <string_view>

static rex::system::object_ref<rex::system::XEvent> native_event;
static unsigned lookups = 0, original_calls = 0;
static rex::system::object_ref<rex::system::XEvent> TestResolveEvent(uint32_t, uint8_t*) {
    ++lookups;
    return rex::system::object_ref<rex::system::XEvent>(native_event);
}
#define CRACKDOWN_EVENT_CLEAR_TEST
#include "../experiments/event_clear.cpp"

// Exact original TU0 helper: clear the guest signal field and clobber r11 only.
REX_EXTERN(__imp__sub_82A4FA70) {
    ++original_calls;
    ctx.r11.u64 = 0;
    REX_STORE_U32(ctx.r3.u32 + 4, 0);
}

static void Check(bool condition, const char* message) {
    if (!condition) {
        std::fprintf(stderr, "Event clear regression failed: %s\n", message);
        std::exit(1);
    }
}

int main(int argc, char** argv) {
    const bool disabled = argc > 1 && std::string_view(argv[1]) == "--disabled";
    Check(!REXCVAR_GET(fix_guest_event_clear), "compiled default must stay disabled");
    const auto* flag = rex::cvar::GetFlagInfo("fix_guest_event_clear");
    Check(flag && flag->lifecycle == rex::cvar::Lifecycle::kRequiresRestart,
          "flag must expose restart-required lifecycle metadata");
    if (!disabled) REXCVAR_SET(fix_guest_event_clear, true);
    alignas(64) std::array<uint8_t, 1024> memory{};
    auto* base = memory.data();
    constexpr uint32_t address = 128;
    auto* header = reinterpret_cast<rex::system::X_DISPATCH_HEADER*>(base + address);
    header->type = 0;  // Real manual-reset event, like the profiled worker's 16 events.
    header->signal_state = 1;
    native_event.reset(new rex::system::XEvent(nullptr));
    native_event->InitializeNative(header, header);
    REX_STORE_U32(address + 8, rex::system::kXObjSignature);

    auto signaled = [] {
        uint32_t state = 0;
        native_event->Query(nullptr, &state);
        return state != 0;
    };
    auto invoke = [&](uint32_t caller) {
        PPCContext ctx{};
        ctx.r3.u32 = address;
        ctx.r11.u64 = 0x1122334455667788ULL;
        ctx.r30.u64 = 0xfedcba9876543210ULL;
        ctx.lr = caller;
        sub_82A4FA70(ctx, base);
        Check(REX_LOAD_U32(address + 4) == 0, "original guest store must execute");
        Check(ctx.r3.u32 == address && ctx.r11.u64 == 0 &&
              ctx.r30.u64 == 0xfedcba9876543210ULL && uint32_t(ctx.lr) == caller,
              "original guest register effects must remain unchanged");
    };
    Check(signaled(), "native event must begin signaled");
    if (disabled) {
        invoke(0x8225C618);  // Cold default off reproduces the stale signal exactly.
        Check(signaled() && lookups == 0, "guest store alone leaves native signal stale");
        Check(rex::cvar::SetFlagByName("fix_guest_event_clear", "true"),
              "SDK may change pending flag value even when restart is required");
        Check(REXCVAR_GET(fix_guest_event_clear), "requested pending value must change");
        for (uint32_t caller : {0x8225C618u, 0x8225C6B8u, 0x8225C6C4u, 0x8225C74Cu})
            invoke(caller);
        Check(signaled() && lookups == 0 && original_calls == 5,
              "cold disabled mode must ignore attempted live enable for all callers");
        native_event.reset();
        std::puts("Cold disabled event clearing stays disabled until process restart");
        return 0;
    }
    invoke(0x12345678);
    Check(signaled() && lookups == 0, "unrelated callers must retain native state");
    for (uint32_t caller : {0x8225C618u, 0x8225C6B8u, 0x8225C6C4u, 0x8225C74Cu}) {
        native_event->Set(0, false);
        header->signal_state = 1;
        invoke(caller);
        Check(!signaled(), "each guarded caller must clear the real host event");
    }
    Check(lookups == 4, "only guarded initialized events should resolve");
    Check(rex::cvar::SetFlagByName("fix_guest_event_clear", "false"),
          "SDK may accept a pending disable request");
    Check(!REXCVAR_GET(fix_guest_event_clear), "requested backing flag must become false");
    native_event->Set(0, false);
    header->signal_state = 1;
    invoke(0x8225C618);
    Check(!signaled() && lookups == 5,
          "effective cold-enabled mode must ignore attempted live disable");
    native_event->Set(0, false);
    REX_STORE_U32(address + 8, 0);
    invoke(0x8225C618);
    Check(signaled() && lookups == 5, "uninitialized guest header must not resolve");
    REX_STORE_U32(address + 8, rex::system::kXObjSignature);
    header->type = 5;  // Semaphore header must not be cast to XEvent.
    invoke(0x8225C618);
    Check(signaled() && lookups == 5, "non-event object must not resolve");
    header->type = 1;
    header->signal_state = 1;
    native_event.reset(new rex::system::XEvent(nullptr));
    native_event->InitializeNative(header, header);
    Check(signaled(), "real auto-reset event must begin signaled");
    invoke(0x8225C618);
    Check(!signaled() && lookups == 6, "real auto-reset event must be cleared too");
    Check(original_calls == 9, "every invocation must call original exactly once");
    native_event.reset();
    std::puts("Guest event clear preserves ABI and synchronizes real SDK native events");
}
