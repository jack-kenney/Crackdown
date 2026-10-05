// TU0's distant crowd retains authored reference-step data. Only its consumed
// displacement, segment countdown, animation interval and steering response
// use elapsed time; path selection and rendering remain original guest code.
#include "generated/crackdown_pch.h"
#include "crowd_timing.h"
#include "crowd_timing_policy.h"
#include <rex/cvar.h>
#include <array>
#include <atomic>
#include <mutex>

REXCVAR_DEFINE_BOOL(normalize_crowd_timing, true, "Experiments",
    "Elapsed-time distant crowd movement, animations and steering; native timing only. Restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

REXCVAR_DEFINE_BOOL(normalize_background_car_timing, true, "Experiments",
    "Elapsed-time low-detail car movement and direction response; native timing only. Restart required.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace {
constexpr uint32_t crowd = 0x82E61D30, pool = crowd + 9056, capacity = 1500, stride = 672;
constexpr uint32_t cars = 0x82FDE4B0, car_pool = cars + 4032, car_capacity = 1000, car_stride = 480;
std::atomic<bool> native_enabled{false};
struct Slot {
    std::mutex mutex;
    uint8_t* mapping = nullptr;
    uint32_t path_a = 0, path_b = 0, path_c = 0, remaining = 0, total_ms = 0;
    double fraction = 0;
    bool valid = false;
};
std::array<Slot, capacity> slots;
std::array<Slot, car_capacity> car_slots;
struct Pending {
    uint8_t* mapping = nullptr;
    uint32_t object = 0, expected = 0, remaining = 0;
    bool valid = false;
};
thread_local Pending pending;
thread_local Pending car_pending;
thread_local uint8_t* current_mapping = nullptr;
struct MappingScope {
    uint8_t* previous = current_mapping;
    explicit MappingScope(uint8_t* mapping) { current_mapping = mapping; }
    ~MappingScope() { current_mapping = previous; }
};

bool Enabled() {
    return native_enabled.load(std::memory_order_relaxed) && REXCVAR_GET(normalize_crowd_timing);
}
bool PoolObject(uint32_t address) {
    return address >= pool && address < pool + capacity * stride && (address - pool) % stride == 0;
}
double Frames(uint8_t* base, uint32_t signature = crowd) {
    if (!base || REX_LOAD_U32(signature) != 0xACED0FF0) return 0;
    const auto step_ms = REX_LOAD_U32(0x82D99128);
    // The native main clock commits at most 100 ms. Malformed or zero clock
    // samples pass through; discarded hitch debt is never added back here.
    return step_ms && step_ms <= 100 ? double(step_ms) * .03 : 0;
}
bool CarsEnabled() {
    return native_enabled.load(std::memory_order_relaxed) && REXCVAR_GET(normalize_background_car_timing);
}
bool CarObject(uint32_t address) {
    return address >= car_pool && address < car_pool + car_capacity * car_stride &&
        (address - car_pool) % car_stride == 0;
}
void ResetSlot(uint32_t address) {
    if (!PoolObject(address)) return;
    auto& slot = slots[(address - pool) / stride];
    std::lock_guard guard(slot.mutex);
    slot.valid = false;
    slot.fraction = 0;
}
}

void crackdown::SetCrowdNativeTiming(bool enabled) {
    native_enabled.store(enabled, std::memory_order_relaxed);
}

void CrackdownCrowdHooks::Advance(const PPCRegister& object, PPCVRegister& displacement) const {
    pending = {};
    const auto address = object.u32;
    if (!Enabled() || !PoolObject(address)) return;
    const double frames = Frames(base);
    if (!frames) return;
    const auto count = REX_LOAD_U32(address + 176);
    if (!count || count > INT32_MAX) return;
    for (unsigned lane = 1; lane < 4; ++lane)
        if (!std::isfinite(displacement.f32[lane])) return;
    const auto node_a = REX_LOAD_U32(address + 160), node_b = REX_LOAD_U32(address + 164);
    const auto total = REX_LOAD_U32(0x82D9912C);
    auto& slot = slots[(address - pool) / stride];
    std::lock_guard guard(slot.mutex);
    if (!slot.valid || slot.mapping != base || slot.path_a != node_a || slot.path_b != node_b ||
        slot.remaining != count || total < slot.total_ms) slot.fraction = 0;
    const auto plan = crackdown::PlanCrowdStep(count, slot.fraction, frames);
    slot.valid = true;
    slot.mapping = base;
    slot.path_a = node_a; slot.path_b = node_b;
    slot.remaining = plan.remaining; slot.fraction = plan.fraction; slot.total_ms = total;
    // PPC VMX spatial lanes map to host f32[3], [2], [1]. Preserve W bits.
    if (plan.displacement_scale != 1)
        for (unsigned lane = 1; lane < 4; ++lane)
            displacement.f32[lane] = float(double(displacement.f32[lane]) * plan.displacement_scale);
    pending = {base, address, count - 1, plan.remaining, true};
}

void CrackdownCrowdHooks::Countdown(const PPCRegister& object, PPCRegister& remaining) const {
    const auto plan = pending;
    pending = {};
    // No calls occur between the audited movement and countdown instructions.
    // Pair with exactly that invocation, never an unrelated register store.
    if (plan.valid && plan.mapping == base && plan.object == object.u32 && remaining.u32 == plan.expected)
        remaining.u64 = plan.remaining;
}

void CrackdownCrowdHooks::Animation(const PPCRegister& object, PPCRegister& interval) const {
    if (!Enabled() || !PoolObject(object.u32)) return;
    const double frames = Frames(base);
    if (frames && frames != 1) interval.f64 = double(float(interval.f64 * frames));
}

void CrackdownCrowdHooks::Steering(PPCRegister& gain) const {
    if (!Enabled()) return;
    const double frames = Frames(base);
    if (frames && frames != 1)
        gain.f64 = double(float(crackdown::CrowdSteeringGain(gain.f64, frames)));
}

// ReXGlue emits matching register-reference declarations at the instruction
// sites. The two audited parent routines establish the current memory mapping
// and always forward the complete guest context exactly once.
void CrackdownCrowdAdvance(PPCRegister& object, PPCVRegister& displacement) {
    CrackdownCrowdHooks{current_mapping}.Advance(object, displacement);
}
void CrackdownCrowdCountdown(PPCRegister& object, PPCRegister& remaining) {
    CrackdownCrowdHooks{current_mapping}.Countdown(object, remaining);
}
void CrackdownCrowdAnimation(PPCRegister& object, PPCRegister& interval) {
    CrackdownCrowdHooks{current_mapping}.Animation(object, interval);
}
void CrackdownCrowdSteering(PPCRegister& gain) {
    CrackdownCrowdHooks{current_mapping}.Steering(gain);
}

REX_EXTERN(__imp__sub_82330038);
REX_EXTERN(sub_82330038) {
    const MappingScope mapping(base);
    __imp__sub_82330038(ctx, base);
}
REX_EXTERN(__imp__sub_82338220);
REX_EXTERN(sub_82338220) {
    const MappingScope mapping(base);
    __imp__sub_82338220(ctx, base);
}

REX_EXTERN(__imp__sub_823315E0);
REX_EXTERN(sub_823315E0) {
    __imp__sub_823315E0(ctx, base);
    // Pool slots are recycled. A new occupant must never inherit fractional
    // path progress from an earlier crowd object at the same address.
    if (Enabled()) ResetSlot(ctx.r3.u32);
}

// Cars live in a separate pool and update before visibility/mesh selection.
// Their original routine smooths direction by 10%, adds direction * authored
// step * multiplier, then consumes one reference segment update per call.
void CrackdownBackgroundCarDirection(PPCRegister& object, PPCRegister& gain) {
    if (!CarsEnabled() || !CarObject(object.u32)) return;
    const double frames = Frames(current_mapping, cars + 4);
    if (frames && frames != 1) {
        auto* base = current_mapping;
        const auto count = REX_LOAD_U32(object.u32 + 124);
        if (!count || count > INT32_MAX) return;
        gain.f64 = double(float(crackdown::CrowdSteeringGain(gain.f64, frames)));
    }
}

void CrackdownBackgroundCarAdvance(PPCRegister& object, PPCVRegister& displacement) {
    car_pending = {};
    const auto address = object.u32;
    if (!CarsEnabled() || !CarObject(address)) return;
    auto* base = current_mapping;
    const double frames = Frames(base, cars + 4);
    if (!frames) return;
    const auto count = REX_LOAD_U32(address + 124);
    if (!count || count > INT32_MAX) return;
    for (unsigned lane = 1; lane < 4; ++lane)
        if (!std::isfinite(displacement.f32[lane])) return;
    const auto a = REX_LOAD_U32(address + 136), b = REX_LOAD_U32(address + 140), c = REX_LOAD_U32(address + 144);
    const auto total = REX_LOAD_U32(0x82D9912C);
    auto& slot = car_slots[(address - car_pool) / car_stride];
    std::lock_guard guard(slot.mutex);
    if (!slot.valid || slot.mapping != base || slot.path_a != a || slot.path_b != b || slot.path_c != c ||
        slot.remaining != count || total < slot.total_ms) slot.fraction = 0;
    const auto plan = crackdown::PlanCrowdStep(count, slot.fraction, frames);
    slot.valid = true; slot.mapping = base;
    slot.path_a = a; slot.path_b = b; slot.path_c = c;
    slot.remaining = plan.remaining; slot.fraction = plan.fraction; slot.total_ms = total;
    // +48 retains the nominal guest step. Scale only the register consumed by
    // the following position addition, leaving authored speed data untouched.
    if (plan.displacement_scale != 1)
        for (unsigned lane = 1; lane < 4; ++lane)
            displacement.f32[lane] = float(double(displacement.f32[lane]) * plan.displacement_scale);
    car_pending = {base, address, count - 1, plan.remaining, true};
}

void CrackdownBackgroundCarCountdown(PPCRegister& object, PPCRegister& remaining) {
    const auto plan = car_pending;
    car_pending = {};
    if (plan.valid && plan.mapping == current_mapping && plan.object == object.u32 && remaining.u32 == plan.expected)
        remaining.u64 = plan.remaining;
}

void CrackdownBackgroundCarAllocated(PPCRegister& object) {
    if (!CarsEnabled() || !CarObject(object.u32)) return;
    auto& slot = car_slots[(object.u32 - car_pool) / car_stride];
    std::lock_guard guard(slot.mutex);
    slot.valid = false; slot.fraction = 0;
}

REX_EXTERN(__imp__sub_82333538);
REX_EXTERN(sub_82333538) {
    const MappingScope mapping(base);
    __imp__sub_82333538(ctx, base);
}
