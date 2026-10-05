// Opt-in observations only. Every hook forwards the original context unchanged.
#include "generated/crackdown_pch.h"
#include "character_step_down.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <fstream>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_map>
#include <vector>

REXCVAR_DEFINE_STRING(physics_trace_path, "", "Experiments",
    "Observe player/NPC controllers and Havok steps in a buffered CSV; empty disables. Diagnostic use only.")
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

namespace {
using Clock = std::chrono::steady_clock;
using Vector = std::array<float, 3>;
struct State {
    Vector position{}, velocity{}, derived{}, intended{};
    uint32_t support = 0, flags = 0;
    Vector controller_position{}, phantom_position{};
};
struct Record {
    const char* kind = "";
    uint32_t actor = 0, caller = 0, step_ms = 0, total_ms = 0;
    uint32_t logical_frame = 0, discarded_ms = 0, player = 0;
    double seconds = 0, duration_ms = 0, input_dt = 0, physics_dt = 0;
    State before{}, after{};
    uint32_t returned_r3 = 0;
    float world_before = 0, world_after = 0;
};
float FloatAt(uint32_t address, uint8_t* base) {
    return std::bit_cast<float>(REX_LOAD_U32(address));
}
Vector VectorAt(uint32_t address, uint8_t* base) {
    return {FloatAt(address, base), FloatAt(address + 4, base), FloatAt(address + 8, base)};
}
State Snapshot(uint32_t actor, uint8_t* base) {
    const auto component = actor + 1344;
    State state{VectorAt(actor + 320, base), VectorAt(actor + 1296, base),
        VectorAt(component + 352, base), VectorAt(component + 560, base),
        REX_LOAD_U32(component + 460),
        uint32_t(REX_LOAD_U8(component + 1001)) << 16 |
        uint32_t(REX_LOAD_U8(component + 1002)) << 8 | REX_LOAD_U8(component + 1003)};
    state.controller_position = VectorAt(component + 336, base);
    // sub_829D3550's position getter follows the phantom's collision object.
    const auto phantom = REX_LOAD_U32(component + 660);
    const auto collision = phantom ? REX_LOAD_U32(phantom + 48) : 0;
    const auto motion = collision ? REX_LOAD_U32(collision + 28) : 0;
    if (motion) state.phantom_position = VectorAt(motion + 48, base);
    return state;
}
double Length(const Vector& v) {
    return std::hypot(double(v[0]), double(v[1]), double(v[2]));
}

class Trace {
public:
    Clock::time_point start = Clock::now();
    std::atomic<bool> available{false};
    explicit Trace(const std::string& path) : path_(path), output_(path) {
        if (!output_) {
            REXLOG_ERROR("Cannot open physics trace: {}", path);
            return;
        }
        output_ << "seconds,kind,actor,caller,player,step_ms,total_ms,logical_frame,discarded_ms,input_dt,physics_dt,duration_ms,world_before,world_after";
        for (const auto* phase : {"before", "after"}) {
            for (const auto* field : {"position", "velocity", "derived", "intended", "controller_position", "phantom_position"}) {
                for (const auto* axis : {"x", "y", "z"}) output_ << ',' << phase << '_' << field << '_' << axis;
            }
            output_ << ',' << phase << "_support," << phase << "_flags";
        }
        output_ << ",returned_r3,dropped_records\n";
        output_.precision(10);
        queue_.reserve(kCapacity);
        worker_ = std::jthread([this](std::stop_token stop) { Drain(stop); });
        available = true;
    }
    ~Trace() {
        available = false;
        if (worker_.joinable()) {
            worker_.request_stop();
            wake_.notify_all();
            worker_.join();
        }
    }
    void Enqueue(const Record& record) {
        if (!available.load(std::memory_order_relaxed)) return;
        // Guest threads never wait for disk output or the background writer.
        std::unique_lock guard(mutex_, std::try_to_lock);
        if (!guard || queue_.size() == kCapacity) {
            ++dropped_;
            return;
        }
        queue_.push_back(record);
    }
private:
    static constexpr size_t kCapacity = 8192;
    std::string path_;
    std::ofstream output_;
    std::mutex mutex_;
    std::condition_variable wake_;
    std::vector<Record> queue_;
    std::atomic<uint64_t> dropped_{0};
    std::jthread worker_;
    void Drain(std::stop_token stop) {
        std::vector<Record> batch;
        batch.reserve(kCapacity);
        uint64_t written = 0;
        while (true) {
            {
                std::unique_lock guard(mutex_);
                wake_.wait_for(guard, std::chrono::milliseconds(100), [&] { return stop.stop_requested(); });
                batch.swap(queue_);
            }
            for (const auto& r : batch) {
                output_ << r.seconds << ',' << r.kind << ',' << std::hex << r.actor << ',' << r.caller
                    << std::dec << ',' << r.player << ',' << r.step_ms << ',' << r.total_ms << ','
                    << r.logical_frame << ',' << r.discarded_ms << ',' << r.input_dt << ','
                    << r.physics_dt << ',' << r.duration_ms << ',' << r.world_before << ',' << r.world_after;
                for (const auto* s : {&r.before, &r.after}) {
                    for (const auto* v : {&s->position, &s->velocity, &s->derived, &s->intended,
                            &s->controller_position, &s->phantom_position}) {
                        for (const auto value : *v) output_ << ',' << value;
                    }
                    output_ << ',' << std::hex << s->support << ',' << s->flags << std::dec;
                }
                output_ << ',' << r.returned_r3 << ',' << dropped_.load(std::memory_order_relaxed) << '\n';
                ++written;
            }
            batch.clear();
            output_.flush();
            if (!output_) {
                available = false;
                break;
            }
            if (stop.stop_requested()) {
                std::lock_guard guard(mutex_);
                if (queue_.empty()) break;
            }
        }
        std::ofstream summary(path_ + ".summary.json");
        summary << "{\"written_records\":" << written << ",\"dropped_records\":" << dropped_.load()
            << ",\"output_ok\":" << (output_ ? "true" : "false") << "}\n";
    }
};

Trace* GetTrace() {
    // The restart-only flag is read once at the first movement/physics call.
    static std::unique_ptr<Trace> trace = [] {
        const auto path = REXCVAR_GET(physics_trace_path);
        if (path.empty()) return std::unique_ptr<Trace>{};
        try { return std::make_unique<Trace>(path); }
        catch (const std::exception& e) {
            REXLOG_ERROR("Physics trace unavailable: {}", e.what());
            return std::unique_ptr<Trace>{};
        }
    }();
    return trace && trace->available.load(std::memory_order_relaxed) ? trace.get() : nullptr;
}
Record Begin(Trace& trace, const char* kind, uint32_t actor, const PPCContext& ctx, uint8_t* base) {
    Record record;
    record.kind = kind; record.actor = actor; record.caller = uint32_t(ctx.lr);
    record.seconds = std::chrono::duration<double>(Clock::now() - trace.start).count();
    record.input_dt = ctx.f1.f64;
    record.step_ms = REX_LOAD_U32(0x82D99128);
    record.total_ms = REX_LOAD_U32(0x82D9912C);
    const auto engine = REX_LOAD_U32(0x82DE25C0);
    if (engine) {
        record.logical_frame = REX_LOAD_U32(engine + 16);
        record.discarded_ms = REX_LOAD_U32(engine + 64);
    }
    const auto player = REX_LOAD_U32(0x82DE3F3C);
    record.player = player && REX_LOAD_U32(player + 292) == actor + 32;
    return record;
}
void FinishMovement(Trace& trace, Record& record, uint8_t* base) {
    record.after = Snapshot(record.actor, base);
    // Observe every local-player call and high-speed call. Ordinary NPC calls
    // are sampled at a 100 ms simulation cadence per actor and path.
    const bool fast = Length(record.before.velocity) >= 35 || Length(record.after.velocity) >= 35 ||
        Length(record.before.derived) >= 35 || Length(record.after.derived) >= 35;
    thread_local std::unordered_map<uint64_t, uint32_t> last;
    const uint64_t key = (uint64_t(record.actor) << 32) | record.caller;
    const auto found = last.find(key);
    const bool periodic = found == last.end() || uint32_t(record.total_ms - found->second) >= 100;
    if (!record.player && !fast && !periodic) return;
    if (last.size() >= 8192) last.clear();
    last[key] = record.total_ms;
    trace.Enqueue(record);
}

template<class Original>
void Movement(const char* kind, bool component, Original original, PPCContext& ctx, uint8_t* base) {
    auto* trace = GetTrace();
    if (!trace) { original(ctx, base); return; }
    const auto actor = component ? ctx.r3.u32 - 1344 : ctx.r3.u32;
    auto record = Begin(*trace, kind, actor, ctx, base);
    record.before = Snapshot(actor, base);
    const auto start = Clock::now();
    original(ctx, base);
    record.duration_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    record.returned_r3 = ctx.r3.u32;
    FinishMovement(*trace, record, base);
}
}

REX_EXTERN(__imp__sub_822B97B0);
REX_EXTERN(sub_822B97B0) {
    Movement("agent_update", false, __imp__sub_822B97B0, ctx, base);
}
REX_EXTERN(__imp__sub_822AB708);
REX_EXTERN(sub_822AB708) {
    Movement("controller_proxy", true, __imp__sub_822AB708, ctx, base);
}
REX_EXTERN(__imp__sub_822A9E78);
REX_EXTERN(sub_822A9E78) {
    Movement("controller_local", true, __imp__sub_822A9E78, ctx, base);
}
// Component arguments verified from the TU0 callers. These observations split
// the local controller into stages without changing its inputs or outputs.
#define TRACE_COMPONENT_STAGE(address, label) \
    REX_EXTERN(__imp__sub_##address); \
    REX_EXTERN(sub_##address) { Movement(label, true, __imp__sub_##address, ctx, base); }
TRACE_COMPONENT_STAGE(822AC7C8, "prepare_motion")
TRACE_COMPONENT_STAGE(822AC9D8, "prepare_support")
TRACE_COMPONENT_STAGE(822ACC08, "prepare_contact")
TRACE_COMPONENT_STAGE(822B4100, "step_up")
TRACE_COMPONENT_STAGE(822ACF68, "local_solve")
TRACE_COMPONENT_STAGE(822AD4F0, "proxy_solve")
TRACE_COMPONENT_STAGE(822B1B60, "update_contact")
TRACE_COMPONENT_STAGE(822AF9C0, "update_support")
TRACE_COMPONENT_STAGE(822B02B0, "update_surface")
TRACE_COMPONENT_STAGE(822B1058, "update_velocity")
TRACE_COMPONENT_STAGE(822B15C8, "update_vertical")
TRACE_COMPONENT_STAGE(822B0C28, "update_water")
#undef TRACE_COMPONENT_STAGE
REX_EXTERN(sub_822B4D40) {
    Movement("step_down", true, crackdown::experiments::StepDownWithCorrection, ctx, base);
}
REX_EXTERN(__imp__sub_829BD048);
REX_EXTERN(sub_829BD048) {
    auto* trace = GetTrace();
    if (!trace) { __imp__sub_829BD048(ctx, base); return; }
    const auto world = ctx.r4.u32;
    auto record = Begin(*trace, "havok_step", 0, ctx, base);
    record.physics_dt = ctx.f2.f64;
    record.world_before = FloatAt(world + 16, base);
    const auto start = Clock::now();
    __imp__sub_829BD048(ctx, base);
    record.duration_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count();
    record.world_after = FloatAt(world + 16, base);
    trace->Enqueue(record);
}
