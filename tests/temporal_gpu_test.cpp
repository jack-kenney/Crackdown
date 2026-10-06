// Executes the exact renderer HLSL on D3D12, with synthetic color/depth.
#define NOMINMAX
#include <d3d12.h>
#include <d3dcompiler.h>
#include <dxgi1_6.h>
#include <rex/graphics/d3d12/temporal_math.h>
#include <rex/graphics/d3d12/temporal_shaders.h>
#include <windows.h>
#include <wrl/client.h>

#include <algorithm>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
using Microsoft::WRL::ComPtr;
using namespace rex::graphics::d3d12::temporal;
void Check(HRESULT hr) {
  if (FAILED(hr))
    throw std::runtime_error("D3D12 HRESULT " + std::to_string(uint32_t(hr)));
}
void Require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
uint16_t Half(float value) {
  // Fixture values are nonnegative finite colors and normalized depth.
  Require(std::isfinite(value) && value >= 0 && value <= 1,
          "Half fixture range");
  if (value < std::ldexp(1.f, -14))
    return uint16_t(std::nearbyint(std::ldexp(value, 24)));
  int exponent;
  float mantissa = std::frexp(value, &exponent);
  return uint16_t((exponent + 14) * 1024 +
                  int(std::nearbyint((mantissa * 2 - 1) * 1024)));
}
float Unhalf(uint16_t bits) {
  unsigned exponent = (bits >> 10) & 31, mantissa = bits & 1023;
  float value =
      exponent == 31 ? (mantissa ? std::numeric_limits<float>::quiet_NaN()
                                 : std::numeric_limits<float>::infinity())
      : exponent     ? std::ldexp(float(1024 + mantissa), int(exponent) - 25)
                     : std::ldexp(float(mantissa), -24);
  return bits & 32768 ? -value : value;
}
size_t PixelBytes(DXGI_FORMAT format, int channels) {
  if (format == DXGI_FORMAT_R10G10B10A2_UNORM) return 4;
  if (format == DXGI_FORMAT_R16G16_FLOAT ||
      format == DXGI_FORMAT_R16G16B16A16_FLOAT)
    return channels * 2;
  return channels * 4;
}
struct Texture {
  ComPtr<ID3D12Resource> resource;
  D3D12_RESOURCE_STATES state = D3D12_RESOURCE_STATE_COMMON;
};
struct GPU {
  ComPtr<ID3D12Device> device;
  ComPtr<ID3D12CommandQueue> queue;
  ComPtr<ID3D12CommandAllocator> allocator;
  ComPtr<ID3D12GraphicsCommandList> list;
  ComPtr<ID3D12Fence> fence;
  ComPtr<ID3D12DescriptorHeap> heap;
  ComPtr<ID3D12RootSignature> root;
  ComPtr<ID3D12PipelineState> capture[3], resolve;
  ComPtr<ID3D12Resource> cb;
  std::vector<ComPtr<ID3D12Resource>> held;
  uint64_t serial = 0;
  UINT increment = 0;
  HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  GPU(bool warp) {
    ComPtr<ID3D12Debug> debug;
    if (SUCCEEDED(D3D12GetDebugInterface(IID_PPV_ARGS(&debug)))) {
      debug->EnableDebugLayer();
      std::cout << "D3D12 debug layer enabled\n";
    } else
      std::cout
          << "D3D12 debug layer unavailable; hardware checks still execute\n";
    ComPtr<IDXGIFactory6> factory;
    Check(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory)));
    ComPtr<IDXGIAdapter1> adapter;
    if (warp)
      Check(factory->EnumWarpAdapter(IID_PPV_ARGS(&adapter)));
    else
      Check(factory->EnumAdapterByGpuPreference(
          0, DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE, IID_PPV_ARGS(&adapter)));
    Check(D3D12CreateDevice(adapter.Get(), D3D_FEATURE_LEVEL_11_0,
                            IID_PPV_ARGS(&device)));
    DXGI_ADAPTER_DESC1 ad{};
    adapter->GetDesc1(&ad);
    std::wcout << L"Adapter: " << ad.Description << L"\n";
    D3D12_COMMAND_QUEUE_DESC q{};
    Check(device->CreateCommandQueue(&q, IID_PPV_ARGS(&queue)));
    Check(device->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT,
                                         IID_PPV_ARGS(&allocator)));
    Check(device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT,
                                    allocator.Get(), nullptr,
                                    IID_PPV_ARGS(&list)));
    Check(list->Close());
    Check(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&fence)));
    D3D12_DESCRIPTOR_HEAP_DESC h{};
    h.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
    h.NumDescriptors = 6;
    h.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    Check(device->CreateDescriptorHeap(&h, IID_PPV_ARGS(&heap)));
    increment = device->GetDescriptorHandleIncrementSize(h.Type);
    D3D12_DESCRIPTOR_RANGE ranges[6]{};
    D3D12_ROOT_PARAMETER p[7]{};
    p[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_CBV;
    for (int i = 0; i < 6; ++i) {
      ranges[i] = {i < 3 ? D3D12_DESCRIPTOR_RANGE_TYPE_SRV
                         : D3D12_DESCRIPTOR_RANGE_TYPE_UAV,
                   1, UINT(i % 3), 0, 0};
      p[i + 1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
      p[i + 1].DescriptorTable = {1, &ranges[i]};
    }
    D3D12_STATIC_SAMPLER_DESC sampler{};
    sampler.Filter = D3D12_FILTER_MIN_MAG_MIP_LINEAR;
    sampler.AddressU = sampler.AddressV = sampler.AddressW =
        D3D12_TEXTURE_ADDRESS_MODE_CLAMP;
    sampler.ComparisonFunc = D3D12_COMPARISON_FUNC_ALWAYS;
    sampler.MaxLOD = D3D12_FLOAT32_MAX;
    D3D12_ROOT_SIGNATURE_DESC rd{7, p, 1, &sampler,
                                 D3D12_ROOT_SIGNATURE_FLAG_NONE};
    ComPtr<ID3DBlob> blob, error;
    Check(D3D12SerializeRootSignature(&rd, D3D_ROOT_SIGNATURE_VERSION_1, &blob,
                                      &error));
    Check(device->CreateRootSignature(0, blob->GetBufferPointer(),
                                      blob->GetBufferSize(),
                                      IID_PPV_ARGS(&root)));
    for (int i = 0; i < 3; ++i) {
      const char* counts[] = {"1", "2", "4"};
      D3D_SHADER_MACRO macros[] = {{"SAMPLES", counts[i]}, {nullptr, nullptr}};
      capture[i] = Pipeline(kDepthShader, macros);
    }
    resolve = Pipeline(kResolveShader, nullptr);
    cb = Buffer(256, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
  }
  ~GPU() { CloseHandle(event); }
  ComPtr<ID3DBlob> Compile(const char* code, const char* target,
                           const D3D_SHADER_MACRO* macros = nullptr) {
    ComPtr<ID3DBlob> b, e;
    HRESULT hr = D3DCompile(
        code, strlen(code), "temporal-test", macros, nullptr, "main", target,
        D3DCOMPILE_ENABLE_STRICTNESS | D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &b,
        &e);
    if (FAILED(hr)) {
      if (e) std::cerr << static_cast<char*>(e->GetBufferPointer());
      Check(hr);
    }
    return b;
  }
  ComPtr<ID3D12PipelineState> Pipeline(const char* code,
                                       const D3D_SHADER_MACRO* macros) {
    auto b = Compile(code, "cs_5_1", macros);
    D3D12_COMPUTE_PIPELINE_STATE_DESC d{};
    d.pRootSignature = root.Get();
    d.CS = {b->GetBufferPointer(), b->GetBufferSize()};
    ComPtr<ID3D12PipelineState> p;
    Check(device->CreateComputePipelineState(&d, IID_PPV_ARGS(&p)));
    return p;
  }
  ComPtr<ID3D12Resource> Buffer(UINT64 size, D3D12_HEAP_TYPE type,
                                D3D12_RESOURCE_STATES state) {
    D3D12_HEAP_PROPERTIES h{};
    h.Type = type;
    D3D12_RESOURCE_DESC d{};
    d.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    d.Width = size;
    d.Height = 1;
    d.DepthOrArraySize = 1;
    d.MipLevels = 1;
    d.SampleDesc.Count = 1;
    d.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    ComPtr<ID3D12Resource> r;
    Check(device->CreateCommittedResource(&h, D3D12_HEAP_FLAG_NONE, &d, state,
                                          nullptr, IID_PPV_ARGS(&r)));
    return r;
  }
  Texture Tex(UINT w, UINT h, DXGI_FORMAT fmt, UINT samples = 1,
              bool depth = false) {
    D3D12_HEAP_PROPERTIES hp{};
    hp.Type = D3D12_HEAP_TYPE_DEFAULT;
    D3D12_RESOURCE_DESC d{};
    d.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    d.Width = w;
    d.Height = h;
    d.DepthOrArraySize = 1;
    d.MipLevels = 1;
    d.Format = fmt;
    d.SampleDesc.Count = samples;
    d.Flags = depth ? D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL
                    : D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
    Texture t;
    Check(device->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &d,
                                          t.state, nullptr,
                                          IID_PPV_ARGS(&t.resource)));
    return t;
  }
  void Begin() {
    Check(allocator->Reset());
    Check(list->Reset(allocator.Get(), nullptr));
  }
  void Finish() {
    Check(list->Close());
    ID3D12CommandList* l[] = {list.Get()};
    queue->ExecuteCommandLists(1, l);
    Check(queue->Signal(fence.Get(), ++serial));
    if (fence->GetCompletedValue() < serial) {
      Check(fence->SetEventOnCompletion(serial, event));
      Require(WaitForSingleObject(event, 10000) == WAIT_OBJECT_0,
              "GPU fence timed out");
    }
    Check(device->GetDeviceRemovedReason());
    held.clear();
  }
  void State(Texture& t, D3D12_RESOURCE_STATES s) {
    if (s == t.state) return;
    D3D12_RESOURCE_BARRIER b{};
    b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b.Transition = {t.resource.Get(), D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES,
                    t.state, s};
    list->ResourceBarrier(1, &b);
    t.state = s;
  }
  D3D12_CPU_DESCRIPTOR_HANDLE CPU(int i) {
    auto h = heap->GetCPUDescriptorHandleForHeapStart();
    constexpr int slots[] = {0, 5, 2, 4, 1, 3};
    h.ptr += slots[i] * increment;
    return h;
  }
  D3D12_GPU_DESCRIPTOR_HANDLE Handle(int i) {
    auto h = heap->GetGPUDescriptorHandleForHeapStart();
    constexpr int slots[] = {0, 5, 2, 4, 1, 3};
    h.ptr += slots[i] * increment;
    return h;
  }
  void SRV(Texture& t, int i, DXGI_FORMAT f = DXGI_FORMAT_UNKNOWN) {
    auto td = t.resource->GetDesc();
    D3D12_SHADER_RESOURCE_VIEW_DESC d{};
    d.Format = f == DXGI_FORMAT_UNKNOWN ? td.Format : f;
    d.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    d.ViewDimension = td.SampleDesc.Count == 1
                          ? D3D12_SRV_DIMENSION_TEXTURE2D
                          : D3D12_SRV_DIMENSION_TEXTURE2DMS;
    if (td.SampleDesc.Count == 1) d.Texture2D.MipLevels = 1;
    device->CreateShaderResourceView(t.resource.Get(), &d, CPU(i));
  }
  void UAV(Texture& t, int i) {
    D3D12_UNORDERED_ACCESS_VIEW_DESC d{};
    d.Format = t.resource->GetDesc().Format;
    d.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
    device->CreateUnorderedAccessView(t.resource.Get(), nullptr, &d, CPU(i));
  }
  void Dispatch(ID3D12PipelineState* pipeline, const Constants& c) {
    void* mapped;
    Check(cb->Map(0, nullptr, &mapped));
    memcpy(mapped, &c, sizeof(c));
    cb->Unmap(0, nullptr);
    ID3D12DescriptorHeap* heaps[] = {heap.Get()};
    list->SetDescriptorHeaps(1, heaps);
    list->SetComputeRootSignature(root.Get());
    list->SetComputeRootConstantBufferView(0, cb->GetGPUVirtualAddress());
    for (int i = 0; i < 6; ++i)
      list->SetComputeRootDescriptorTable(i + 1, Handle(i));
    list->SetPipelineState(pipeline);
    list->Dispatch((c.width + 7) / 8, (c.height + 7) / 8, 1);
  }
  void Upload(Texture& t, const std::vector<float>& values, int channels) {
    auto desc = t.resource->GetDesc();
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT fp;
    UINT64 size;
    device->GetCopyableFootprints(&desc, 0, 1, 0, &fp, nullptr, nullptr, &size);
    auto b =
        Buffer(size, D3D12_HEAP_TYPE_UPLOAD, D3D12_RESOURCE_STATE_GENERIC_READ);
    void* mapped;
    Check(b->Map(0, nullptr, &mapped));
    for (UINT y = 0; y < desc.Height; ++y) {
      auto* row = static_cast<char*>(mapped) + y * fp.Footprint.RowPitch;
      for (UINT x = 0; x < desc.Width; ++x) {
        const float* pixel = values.data() + (y * desc.Width + x) * channels;
        char* dst = row + x * PixelBytes(desc.Format, channels);
        if (desc.Format == DXGI_FORMAT_R10G10B10A2_UNORM) {
          uint32_t packed = uint32_t(std::nearbyint(pixel[0] * 1023)) |
                            (uint32_t(std::nearbyint(pixel[1] * 1023)) << 10) |
                            (uint32_t(std::nearbyint(pixel[2] * 1023)) << 20) |
                            (uint32_t(std::nearbyint(pixel[3] * 3)) << 30);
          memcpy(dst, &packed, 4);
        } else if (PixelBytes(desc.Format, channels) == size_t(channels * 2)) {
          for (int k = 0; k < channels; ++k) {
            uint16_t half = Half(pixel[k]);
            memcpy(dst + k * 2, &half, 2);
          }
        } else
          memcpy(dst, pixel, channels * 4);
      }
    }
    b->Unmap(0, nullptr);
    State(t, D3D12_RESOURCE_STATE_COPY_DEST);
    D3D12_TEXTURE_COPY_LOCATION src{b.Get(),
                                    D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT};
    src.PlacedFootprint = fp;
    D3D12_TEXTURE_COPY_LOCATION dst{t.resource.Get(),
                                    D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX};
    list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    held.push_back(b);
  }
  std::vector<float> Read(Texture& t, int channels) {
    auto desc = t.resource->GetDesc();
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT fp;
    UINT64 size;
    device->GetCopyableFootprints(&desc, 0, 1, 0, &fp, nullptr, nullptr, &size);
    auto b =
        Buffer(size, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    Begin();
    State(t, D3D12_RESOURCE_STATE_COPY_SOURCE);
    D3D12_TEXTURE_COPY_LOCATION dst{b.Get(),
                                    D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT};
    dst.PlacedFootprint = fp;
    D3D12_TEXTURE_COPY_LOCATION src{t.resource.Get(),
                                    D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX};
    list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    Finish();
    void* mapped;
    Check(b->Map(0, nullptr, &mapped));
    std::vector<float> result(desc.Width * desc.Height * channels);
    for (UINT y = 0; y < desc.Height; ++y) {
      const char* row = static_cast<char*>(mapped) + y * fp.Footprint.RowPitch;
      for (UINT x = 0; x < desc.Width; ++x) {
        float* pixel = result.data() + (y * desc.Width + x) * channels;
        const char* src = row + x * PixelBytes(desc.Format, channels);
        if (desc.Format == DXGI_FORMAT_R10G10B10A2_UNORM) {
          uint32_t packed;
          memcpy(&packed, src, 4);
          for (int k = 0; k < 3; ++k)
            pixel[k] = ((packed >> (k * 10)) & 1023) / 1023.f;
          pixel[3] = (packed >> 30) / 3.f;
        } else if (PixelBytes(desc.Format, channels) == size_t(channels * 2)) {
          for (int k = 0; k < channels; ++k) {
            uint16_t half;
            memcpy(&half, src + k * 2, 2);
            pixel[k] = Unhalf(half);
          }
        } else
          memcpy(pixel, src, channels * 4);
      }
    }
    b->Unmap(0, nullptr);
    return result;
  }
  void NoErrors() {
    ComPtr<ID3D12InfoQueue> info;
    if (FAILED(device.As(&info))) return;
    for (UINT64 i = 0; i < info->GetNumStoredMessages(); ++i) {
      SIZE_T n = 0;
      info->GetMessage(i, nullptr, &n);
      std::vector<char> b(n);
      auto* m = reinterpret_cast<D3D12_MESSAGE*>(b.data());
      info->GetMessage(i, m, &n);
      if (m->Severity <= D3D12_MESSAGE_SEVERITY_ERROR) {
        std::cerr << m->pDescription << "\n";
        throw std::runtime_error("D3D12 debug-layer error");
      }
    }
  }
  void DrawSampleDepth(Texture& depth, UINT samples) {
    const char* vs =
        "float4 main(uint id:SV_VertexID):SV_Position {return float4(id==2 ? "
        "3:-1,id==1 ? 3:-1,0,1);}";
    const char* ps =
        "float main(uint sample:SV_SampleIndex):SV_Depth {return "
        "0.2+sample*0.1;}";
    auto vb = Compile(vs, "vs_5_1"), pb = Compile(ps, "ps_5_1");
    D3D12_GRAPHICS_PIPELINE_STATE_DESC d{};
    d.pRootSignature = root.Get();
    d.VS = {vb->GetBufferPointer(), vb->GetBufferSize()};
    d.PS = {pb->GetBufferPointer(), pb->GetBufferSize()};
    d.SampleMask = UINT_MAX;
    d.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    d.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    d.RasterizerState.DepthClipEnable = TRUE;
    d.RasterizerState.MultisampleEnable = TRUE;
    d.DepthStencilState.DepthEnable = TRUE;
    d.DepthStencilState.DepthWriteMask = D3D12_DEPTH_WRITE_MASK_ALL;
    d.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC_ALWAYS;
    d.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    d.DSVFormat = DXGI_FORMAT_D32_FLOAT;
    d.SampleDesc.Count = samples;
    ComPtr<ID3D12PipelineState> pipeline;
    Check(device->CreateGraphicsPipelineState(&d, IID_PPV_ARGS(&pipeline)));
    D3D12_DESCRIPTOR_HEAP_DESC hd{};
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_DSV;
    hd.NumDescriptors = 1;
    ComPtr<ID3D12DescriptorHeap> dh;
    Check(device->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&dh)));
    D3D12_DEPTH_STENCIL_VIEW_DESC dd{};
    dd.Format = DXGI_FORMAT_D32_FLOAT;
    dd.ViewDimension = D3D12_DSV_DIMENSION_TEXTURE2DMS;
    auto handle = dh->GetCPUDescriptorHandleForHeapStart();
    device->CreateDepthStencilView(depth.resource.Get(), &dd, handle);
    Begin();
    State(depth, D3D12_RESOURCE_STATE_DEPTH_WRITE);
    list->ClearDepthStencilView(handle, D3D12_CLEAR_FLAG_DEPTH, 0, 0, 0,
                                nullptr);
    D3D12_VIEWPORT viewport{0, 0, 17, 15, 0, 1};
    D3D12_RECT scissor{0, 0, 17, 15};
    list->RSSetViewports(1, &viewport);
    list->RSSetScissorRects(1, &scissor);
    list->OMSetRenderTargets(0, nullptr, FALSE, &handle);
    list->SetGraphicsRootSignature(root.Get());
    list->SetPipelineState(pipeline.Get());
    list->IASetPrimitiveTopology(D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    list->DrawInstanced(3, 1, 0, 0);
    Finish();
  }
};
int main(int argc, char** argv) {
  try {
    GPU g(argc > 1 && std::string(argv[1]) == "--warp");
    Matrix identity{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1}, inv;
    Require(Invert(identity, inv) && inv == identity, "Identity inversion");
    Matrix singular{};
    Require(!Invert(singular, inv), "Singular matrix accepted");
    auto bad = identity;
    bad[0] = std::numeric_limits<float>::infinity();
    Require(!Invert(bad, inv), "Non-finite camera accepted");
    Matrix camera{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, .5f, 0, 0, 1, 0};
    Require(ContinuousCamera(camera, camera), "Stationary camera rejected");
    auto tile = camera;
    for (int i = 0; i < 4; ++i) tile[4 + i] += camera[12 + i] * (960.f / 720);
    Require(TileOffsetY(camera, tile, 720) == 480,
            "EDRAM tile offset recovery");
    tile[0] *= 1.5f;
    Require(TileOffsetY(camera, tile, 720) == -1,
            "Unrelated tile camera accepted");
    auto cut = camera;
    cut[3] = 100;
    Require(!ContinuousCamera(cut, camera), "Teleport did not reset history");
    float scale[] = {1, -1, 1}, offset[] = {.001f, .002f, 0};
    uint32_t vp_origin[] = {10, 20}, vp_size[] = {26, 22},
             crop_origin[] = {12, 24}, crop_size[] = {13, 11};
    auto corrected = CropProjection(identity, scale, offset, vp_origin, vp_size,
                                    0, .5f, crop_origin, crop_size);
    Require(std::abs(corrected[0] - 2) < 1e-6 &&
                std::abs(corrected[5] + 2) < 1e-6 &&
                std::abs(corrected[10] - .5f) < 1e-6,
            "Viewport/depth scale correction");
    Require(std::abs(corrected[3] - (.002f + 1 - 4.f / 13)) < 1e-6 &&
                std::abs(corrected[7] - (.004f - 1 + 8.f / 11)) < 1e-6,
            "Viewport crop origin correction");
    Constants c{};
    c.inverse_current = identity;
    c.previous = identity;
    c.width = 13;
    c.height = 11;
    c.reversed_depth = 1;
    auto source = g.Tex(17, 15, DXGI_FORMAT_R32_FLOAT),
         depth = g.Tex(13, 11, DXGI_FORMAT_R32_FLOAT);
    auto color = g.Tex(13, 11, DXGI_FORMAT_R32G32B32A32_FLOAT),
         old = g.Tex(13, 11, DXGI_FORMAT_R32G32B32A32_FLOAT);
    auto output = g.Tex(13, 11, DXGI_FORMAT_R32G32B32A32_FLOAT),
         next = g.Tex(13, 11, DXGI_FORMAT_R32G32B32A32_FLOAT),
         motion = g.Tex(13, 11, DXGI_FORMAT_R32G32_FLOAT);
    std::vector<float> values(17 * 15);
    for (int y = 0; y < 15; ++y)
      for (int x = 0; x < 17; ++x) values[y * 17 + x] = float(x + y * 17) / 300;
    values[2 * 17 + 1] = std::numeric_limits<float>::quiet_NaN();
    values[2 * 17 + 2] = 2;
    c.origin_x = 1;
    c.origin_y = 2;
    g.Begin();
    g.Upload(source, values, 1);
    g.State(source, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    g.State(depth, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    g.SRV(source, 0);
    g.UAV(depth, 3);
    g.Dispatch(g.capture[0].Get(), c);
    g.Finish();
    auto cropped = g.Read(depth, 1);
    for (int y = 0; y < 11; ++y)
      for (int x = 0; x < 13; ++x) {
        float expected = values[(y + 2) * 17 + x + 1];
        if (!std::isfinite(expected) || expected > 1) expected = 0;
        Require(std::abs(cropped[y * 13 + x] - expected) < 1e-6,
                "Depth crop/sanitize mismatch");
      }
    std::cout << "PASS depth crop, padded target, odd dimensions, NaN/range "
                 "sanitize\n";
    // Two dispatches assemble overlapping EDRAM tiles without stretching them.
    c.height = 6;
    c.padding = 0;
    g.Begin();
    g.State(source, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
    g.State(depth, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    g.SRV(source, 0);
    g.UAV(depth, 3);
    g.Dispatch(g.capture[0].Get(), c);
    g.Finish();
    c.height = 6;
    c.padding = 5;
    c.origin_y = 7;
    g.Begin();
    g.State(depth, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    g.SRV(source, 0);
    g.UAV(depth, 3);
    g.Dispatch(g.capture[0].Get(), c);
    g.Finish();
    cropped = g.Read(depth, 1);
    for (int y = 0; y < 11; ++y)
      for (int x = 0; x < 13; ++x) {
        float expected = values[(y + 2) * 17 + x + 1];
        if (!std::isfinite(expected) || expected > 1) expected = 0;
        Require(std::abs(cropped[y * 13 + x] - expected) < 1e-6,
                "Tiled depth assembly mismatch");
      }
    std::cout << "PASS overlapping EDRAM depth tile assembly\n";
    c.height = 11;
    c.padding = 0;
    c.origin_y = 2;
    for (UINT samples : {2u, 4u}) {
      D3D12_FEATURE_DATA_MULTISAMPLE_QUALITY_LEVELS support{
          DXGI_FORMAT_D32_FLOAT, samples,
          D3D12_MULTISAMPLE_QUALITY_LEVELS_FLAG_NONE, 0};
      Check(g.device->CheckFeatureSupport(
          D3D12_FEATURE_MULTISAMPLE_QUALITY_LEVELS, &support, sizeof(support)));
      if (!support.NumQualityLevels) {
        std::cout << "SKIP " << samples
                  << "x depth samples (device unsupported)\n";
        continue;
      }
      auto multisampled =
          g.Tex(17, 15, DXGI_FORMAT_R32_TYPELESS, samples, true);
      g.DrawSampleDepth(multisampled, samples);
      for (UINT reversed : {0u, 1u}) {
        c.reversed_depth = reversed;
        g.Begin();
        g.State(multisampled, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        g.State(depth, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        g.SRV(multisampled, 0, DXGI_FORMAT_R32_FLOAT);
        g.UAV(depth, 3);
        g.Dispatch(g.capture[samples == 2 ? 1 : 2].Get(), c);
        g.Finish();
        auto pixels = g.Read(depth, 1);
        for (float value : pixels)
          Require(std::abs(value -
                           (reversed ? .2f + (samples - 1) * .1f : .2f)) < 1e-6,
                  "Nearest MSAA depth mismatch");
      }
      std::cout << "PASS " << samples
                << "x nearest depth, forward and reversed Z\n";
    }
    c.reversed_depth = 1;
    c.origin_x = c.origin_y = 0;
    c.valid_history = 1;
    std::vector<float> d(13 * 11, 0.5f), rgb(13 * 11 * 4), history(rgb.size());
    for (int y = 0; y < 11; ++y)
      for (int x = 0; x < 13; ++x) {
        int i = (y * 13 + x) * 4;
        rgb[i] = x / 20.f;
        rgb[i + 1] = y / 20.f;
        rgb[i + 2] = .25;
        rgb[i + 3] = 1;
        for (int k = 0; k < 3; ++k) history[i + k] = rgb[i + k] + .02f;
        history[i + 3] = .5f;
      }
    auto run = [&]() {
      g.Begin();
      g.Upload(depth, d, 1);
      g.Upload(color, rgb, 4);
      g.Upload(old, history, 4);
      g.State(depth, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
      g.State(color, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
      g.State(old, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
      g.State(output, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
      g.State(next, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
      g.State(motion, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
      g.SRV(depth, 0);
      g.SRV(color, 1);
      g.SRV(old, 2);
      g.UAV(output, 3);
      g.UAV(next, 4);
      g.UAV(motion, 5);
      g.Dispatch(g.resolve.Get(), c);
      g.Finish();
    };
    c.mode = 0;
    run();
    auto result = g.Read(output, 4);
    Require(result == rgb, "Passthrough changed color");
    auto mv = g.Read(motion, 2);
    for (float v : mv)
      Require(std::abs(v) < 1e-5, "Stationary camera generated motion");
    std::cout << "PASS passthrough and stationary camera vectors\n";
    // Previous clip X displaced by 2/width produces +1 pixel, UV points current
    // to previous.
    c.previous[3] = 2.f / 13;
    run();
    mv = g.Read(motion, 2);
    for (int y = 0; y < 11; ++y)
      for (int x = 0; x < 12; ++x) {
        Require(std::abs(mv[(y * 13 + x) * 2] - 1) < 1e-5,
                "Motion direction/scale mismatch");
        Require(std::abs(mv[(y * 13 + x) * 2 + 1]) < 1e-5,
                "Unexpected Y motion");
      }
    std::cout << "PASS camera reprojection sign and pixel units\n";
    c.previous = identity;
    c.mode = 3;
    run();
    result = g.Read(output, 4);
    int center = (5 * 13 + 6) * 4;
    Require(result[center] > rgb[center] && result[center] < history[center],
            "History did not accumulate");
    for (size_t i = 3; i < history.size(); i += 4) history[i] = .1f;
    run();
    result = g.Read(output, 4);
    Require(result == rgb, "Disoccluded history was accepted");
    c.valid_history = 0;
    for (size_t i = 3; i < history.size(); i += 4) history[i] = .5f;
    run();
    result = g.Read(output, 4);
    Require(result == rgb, "First frame used history");
    c.valid_history = 1;
    c.previous[15] = -1;
    run();
    mv = g.Read(motion, 2);
    for (float v : mv)
      Require(v == 0, "Behind-camera projection generated vectors");
    std::cout << "PASS history accumulation, disocclusion, reset and invalid "
                 "projection rejection\n";
    // Exercise the live formats, ping-pong barriers, subnormal reverse depth,
    // reset followed by accumulation, and sustained resource reuse.
    color = g.Tex(13, 11, DXGI_FORMAT_R10G10B10A2_UNORM);
    output = g.Tex(13, 11, DXGI_FORMAT_R10G10B10A2_UNORM);
    old = g.Tex(13, 11, DXGI_FORMAT_R16G16B16A16_FLOAT);
    next = g.Tex(13, 11, DXGI_FORMAT_R16G16B16A16_FLOAT);
    motion = g.Tex(13, 11, DXGI_FORMAT_R16G16_FLOAT);
    c.previous = identity;
    c.mode = 3;
    c.valid_history = 0;
    std::fill(d.begin(), d.end(), 0.00001f);
    for (size_t i = 3; i < history.size(); i += 4) history[i] = d[0];
    run();
    const auto initial = g.Read(output, 4);
    for (int frame = 0; frame < 256; ++frame) {
      std::swap(old, next);
      g.Begin();
      c.valid_history = 1;
      g.State(depth, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
      g.State(color, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
      g.State(old, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
      g.State(output, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
      g.State(next, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
      // motion remains UAV across dispatches and needs a UAV barrier.
      D3D12_RESOURCE_BARRIER barrier{};
      barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
      barrier.UAV.pResource = motion.resource.Get();
      g.list->ResourceBarrier(1, &barrier);
      g.SRV(depth, 0);
      g.SRV(color, 1);
      g.SRV(old, 2);
      g.UAV(output, 3);
      g.UAV(next, 4);
      g.UAV(motion, 5);
      g.Dispatch(g.resolve.Get(), c);
      g.Finish();
    }
    result = g.Read(output, 4);
    mv = g.Read(motion, 2);
    auto finalHistory = g.Read(next, 4);
    for (size_t i = 0; i < result.size(); ++i)
      Require(std::isfinite(result[i]) &&
                  std::abs(result[i] - initial[i]) <= 1.1f / 1023,
              "Live-format history drift");
    for (float v : mv)
      Require(std::abs(v) < 1e-5, "Half motion stationary mismatch");
    for (size_t i = 3; i < finalHistory.size(); i += 4)
      Require(std::abs(finalHistory[i] - d[0]) < 6e-8,
              "Subnormal half depth mismatch");
    std::cout << "PASS 256 live-format history frames (RGB10, RG16F, RGBA16F), "
                 "bounded resource reuse\n";
    g.NoErrors();
    std::cout << "All temporal GPU checks passed\n";
    return 0;
  } catch (const std::exception& e) {
    std::cerr << "FAIL: " << e.what() << "\n";
    return 1;
  }
}
