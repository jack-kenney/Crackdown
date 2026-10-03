#include "cache.h"

#include <array>
#include <atomic>
#include <cstdio>
#include <limits>
#include <thread>

using rex::X_STATUS;
static int failures = 0;
#define CHECK(expression) do { if (!(expression)) { \
    std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #expression); ++failures; \
} } while (false)

int main()
{
    CacheFileData data;
    const std::array<uint8_t, 4> payload{1, 2, 3, 4};
    size_t bytes = 99;
    CHECK(data.WriteSync(payload.data(), payload.size(), 0, &bytes) == X_STATUS_SUCCESS);
    CHECK(bytes == 4 && data.GetSize() == 4);
    CHECK(data.WriteSync(payload.data(), payload.size(), 4, &bytes) == X_STATUS_SUCCESS);
    CHECK(bytes == 4 && data.GetSize() == 8);
    CHECK(data.WriteSync(payload.data(), payload.size(), 12, &bytes) == X_STATUS_SUCCESS);
    std::array<uint8_t, 16> read{};
    CHECK(data.ReadSync(read.data(), read.size(), 0, &bytes) == X_STATUS_SUCCESS);
    CHECK(bytes == 16 && read[8] == 0 && read[11] == 0 && read[12] == 1);
    CHECK(data.ReadSync(read.data(), read.size(), 16, &bytes) == X_STATUS_END_OF_FILE);
    CHECK(bytes == 0);
    CHECK(data.ReadSync(nullptr, 0, 16, &bytes) == X_STATUS_SUCCESS && bytes == 0);
    CHECK(data.WriteSync(nullptr, 0, 100, &bytes) == X_STATUS_SUCCESS && bytes == 0);
    CHECK(data.GetSize() == 16);
    CHECK(data.WriteSync(payload.data(), 4, std::numeric_limits<size_t>::max(), &bytes) == X_STATUS_INVALID_PARAMETER);
    CHECK(bytes == 0 && data.GetSize() == 16);
    CHECK(data.SetLength(3) == X_STATUS_SUCCESS && data.GetSize() == 3);
    CHECK(data.SetLength(6) == X_STATUS_SUCCESS);
    CHECK(data.ReadSync(read.data(), 16, 0, &bytes) == X_STATUS_SUCCESS && bytes == 6);
    CHECK(read[0] == 1 && read[2] == 3 && read[3] == 0 && read[5] == 0);

    CacheDevice device("\\CACHE_TEST");
    CHECK(device.Initialize());
    auto* root = device.ResolvePath("");
    CHECK(root != nullptr);
    root->update();
    CHECK(root->size() == 0);
    auto* directory = root->CreateEntry("nested", rex::filesystem::kFileAttributeDirectory);
    directory->update();
    auto* entry = directory->CreateEntry("test.bin", rex::filesystem::kFileAttributeNormal);
    CHECK(entry->path() == "nested\\test.bin");
    CHECK(entry->absolute_path() == "\\CACHE_TEST\\nested\\test.bin");
    CHECK(entry->size() == 0 && entry->allocation_size() == 0);
    rex::filesystem::File* file = nullptr;
    CHECK(entry->Open(rex::filesystem::FileAccess::kGenericRead | rex::filesystem::FileAccess::kGenericWrite, &file) == X_STATUS_SUCCESS);
    CHECK(file->SetLength(4096) == X_STATUS_SUCCESS);
    entry->update();
    CHECK(entry->size() == 4096 && entry->allocation_size() == 4096);
    CHECK(file->WriteSync(payload.data(), 4, 4096, &bytes) == X_STATUS_SUCCESS);
    entry->update();
    CHECK(entry->size() == 4100 && entry->allocation_size() == 4608);
    CHECK(directory->Delete(entry));
    CHECK(directory->GetChild("test.bin") == nullptr);
    CHECK(file->ReadSync(read.data(), 4, 4096, &bytes) == X_STATUS_SUCCESS && bytes == 4);
    file->Destroy();

    CHECK(directory->Open(rex::filesystem::FileAccess::kGenericRead, &file) == X_STATUS_SUCCESS);
    CHECK(file->ReadSync(read.data(), 4, 0, &bytes) == X_STATUS_FILE_IS_A_DIRECTORY && bytes == 0);
    CHECK(file->SetLength(4) == X_STATUS_FILE_IS_A_DIRECTORY);
    file->Destroy();
    entry = root->CreateEntry("readonly.bin", rex::filesystem::kFileAttributeNormal);
    CHECK(entry->Open(rex::filesystem::FileAccess::kGenericRead, &file) == X_STATUS_SUCCESS);
    CHECK(file->WriteSync(payload.data(), 4, 0, &bytes) == X_STATUS_ACCESS_DENIED && bytes == 0);
    CHECK(file->SetLength(4) == X_STATUS_ACCESS_DENIED);
    file->Destroy();

    // Overlap reads, writes and resizing on shared data; contention must not
    // become ACCESS_DENIED, and resize must not invalidate an active copy.
    std::atomic<int> thread_failures{0};
    std::thread writer([&] {
        for (int i = 0; i < 10000; ++i) {
            size_t count = 0;
            if (data.WriteSync(payload.data(), 4, 0, &count) != X_STATUS_SUCCESS || count != 4)
                ++thread_failures;
        }
    });
    std::thread reader([&] {
        std::array<uint8_t, 128> buffer{};
        for (int i = 0; i < 10000; ++i) {
            size_t count = 0;
            auto status = data.ReadSync(buffer.data(), buffer.size(), 0, &count);
            if (status != X_STATUS_SUCCESS && status != X_STATUS_END_OF_FILE) ++thread_failures;
            if (count > buffer.size()) ++thread_failures;
        }
    });
    std::thread resizer([&] {
        for (int i = 0; i < 10000; ++i) {
            if (data.SetLength(i % 2 ? 128 : 0) != X_STATUS_SUCCESS) ++thread_failures;
            (void)data.GetSize();
        }
    });
    writer.join(); reader.join(); resizer.join();
    CHECK(thread_failures == 0);
    std::printf("Cache regression tests: %d failures\n", failures);
    return failures ? 1 : 0;
}
