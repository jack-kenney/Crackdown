// TU0 native vehicle mesh transition override. Experimental build only.
#include "crackdown_pch.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <bit>
#include <cmath>
#include <mutex>

REXCVAR_DEFINE_DOUBLE(vehicle_lod1_distance, 0.0, "Experiments",
    "Vehicle first mesh LOD transition in meters (0 preserves the original game). "
    "Lower values reduce distant vehicle detail; restart required.")
    .range(0.0, 100.0)
    .lifecycle(rex::cvar::Lifecycle::kRequiresRestart);

REX_EXTERN(__imp__sub_823B6518);
REX_EXTERN(sub_823B6518) {
    const double distance = REXCVAR_GET(vehicle_lod1_distance);
    const uint32_t clump = ctx.r4.u32;
    if (std::isfinite(distance) && distance > 0.0 && distance <= 100.0 &&
        clump && REX_LOAD_U32(clump + 1680) == 2) {
        // The shipped SetVehicleLod1DistOveride command writes these exact
        // globals. The original selector caps only category-2's first mesh
        // threshold to this value and retains its blend, later LODs and cull
        // distance. In a live original session this was enabled at 25 meters.
        // Apply once, at the audited rendering consumer after startup. Never
        // alter per-clump globals temporarily or touch crowd spawn/AI state.
        static std::once_flag configured;
        std::call_once(configured, [base, distance] {
            REX_STORE_U32(0x82BAA3B8, std::bit_cast<uint32_t>(float(distance)));
            REX_STORE_U8(0x82BAA3B4, 1);
            REXLOG_INFO("Vehicle mesh LOD experiment: first transition capped at {} meters", distance);
        });
    }
    __imp__sub_823B6518(ctx, base);
}
