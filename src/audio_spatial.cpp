#include <algorithm>

#include "crackdown_pch.h"

extern "C" REX_FUNC(__imp__sub_8225F910);

// TU0's positional-audio vector acos helper receives normalized dot products.
// Host rounding can put a dot product one ULP outside [-1, 1]. Its reciprocal
// square-root refinement then evaluates 0 * infinity (or sqrt of a negative
// number), poisoning the channel gain and persistent reverb history with NaNs.
// Clamp the input domain and retain the guest approximation and ABI effects.
REX_EXTERN(sub_8225F910) {
    for (float& value : ctx.v1.f32) {
        value = std::clamp(value, -1.0f, 1.0f);
    }
    __imp__sub_8225F910(ctx, base);
}
