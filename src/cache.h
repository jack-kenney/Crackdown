#pragma once

#include <memory>
#include <mutex>
#include <vector>

#include <rex/filesystem/device.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/file.h>

class CacheEntry;

class CacheFileData
{
public:
	void Destroy();

	rex::X_STATUS ReadSync(void* buffer, size_t buffer_length, size_t byte_offset, size_t* out_bytes_read);
	rex::X_STATUS WriteSync(const void* buffer, size_t buffer_length, size_t byte_offset, size_t* out_bytes_written);

	rex::X_STATUS SetLength(size_t length);

	size_t GetSize() const;

private:
	std::vector<uint8_t> data_{};
	mutable std::mutex mutex_{};
};

class CacheFile : public rex::filesystem::File
{
public:
	CacheFile(uint32_t file_access, CacheEntry* entry, std::shared_ptr<CacheFileData> data);

	void Destroy() override;

	rex::X_STATUS ReadSync(std::span<uint8_t> buffer, size_t byte_offset, size_t* out_bytes_read) override;
	rex::X_STATUS WriteSync(std::span<const uint8_t> buffer, size_t byte_offset, size_t* out_bytes_written) override;

	rex::X_STATUS SetLength(size_t length) override;

private:
	std::shared_ptr<CacheFileData> data_{};
};

class CacheEntry : public rex::filesystem::Entry
{
public:
	static CacheEntry* Create(rex::filesystem::Device* device, Entry* parent, const std::string_view path, uint32_t attribute);
	rex::X_STATUS Open(uint32_t desired_access, rex::filesystem::File** out_file) override;

	void update() override;

	std::unique_ptr<Entry> CreateEntryInternal(const std::string_view name, uint32_t attributes) override;
	bool DeleteEntryInternal(Entry* entry) override;

protected:
	CacheEntry(rex::filesystem::Device* device, Entry* parent, const std::string_view path);

private:
	friend class CacheDevice;

	std::shared_ptr<CacheFileData> data_{};
};

class CacheDevice : public rex::filesystem::Device
{
public:

	CacheDevice(const std::string_view mount_path);
	~CacheDevice() override = default;

	bool Initialize() override;

	bool is_read_only() const override { return false; }

	void Dump(rex::string::StringBuffer* string_buffer) override;
	rex::filesystem::Entry* ResolvePath(const std::string_view path) override;

	const std::string& name() const override { return name_; }
	uint32_t attributes() const override { return 0; }
	uint32_t component_name_max_length() const override { return 40; }

	uint32_t total_allocation_units() const override { return 128 * 1024; }
	uint32_t available_allocation_units() const override { return 128 * 1024; }
	uint32_t sectors_per_allocation_unit() const override { return 1; }
	uint32_t bytes_per_sector() const override { return 0x200; }

private:
	std::unique_ptr<rex::filesystem::Entry> root_entry_;
	std::string name_;
};
