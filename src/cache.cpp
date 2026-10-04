#include "cache.h"

#include <algorithm>
#include <cstring>
#include <new>

#include <rex/logging.h>
#include <rex/math.h>
#include <rex/string.h>

// Allows use of X_STATUS macros, otherwise it can't find the X_STATUS type used. 
using rex::X_STATUS;

void CacheFileData::Destroy()
{
	std::lock_guard lock(mutex_);
	data_.clear();
}

size_t CacheFileData::GetSize() const
{
	std::lock_guard lock(mutex_);
	return data_.size();
}

X_STATUS CacheFileData::ReadSync(void* buffer, size_t buffer_length, size_t byte_offset, size_t* out_bytes_read)
{
	if (out_bytes_read) *out_bytes_read = 0;
	if (!buffer_length) return X_STATUS_SUCCESS;
	if (!buffer) return X_STATUS_INVALID_PARAMETER;
	std::lock_guard lock(mutex_);
	if (byte_offset >= data_.size()) {
		return X_STATUS_END_OF_FILE;
	}

	size_t real_length = std::min(buffer_length, data_.size() - byte_offset);
	std::memcpy(buffer, data_.data() + byte_offset, real_length);
	if (out_bytes_read) *out_bytes_read = real_length;

	return X_STATUS_SUCCESS;
}

X_STATUS CacheFileData::WriteSync(const void* buffer, size_t buffer_length, size_t byte_offset, size_t* out_bytes_written)
{
	if (out_bytes_written) *out_bytes_written = 0;
	if (!buffer_length) return X_STATUS_SUCCESS;
	if (!buffer) return X_STATUS_INVALID_PARAMETER;
	std::lock_guard lock(mutex_);
	if (buffer_length > data_.max_size() || byte_offset > data_.max_size() - buffer_length)
		return X_STATUS_INVALID_PARAMETER;

	auto writeEnd = byte_offset + buffer_length;
	if (writeEnd > data_.size())
	{
		try { data_.resize(writeEnd); }
		catch (const std::bad_alloc&) { return X_STATUS_NO_MEMORY; }
	}

	std::memcpy(data_.data() + byte_offset, buffer, buffer_length);
	if (out_bytes_written) *out_bytes_written = buffer_length;

	return X_STATUS_SUCCESS;
}

X_STATUS CacheFileData::SetLength(size_t length)
{
	std::lock_guard lock(mutex_);
	if (length > data_.max_size()) return X_STATUS_INVALID_PARAMETER;
	try { data_.resize(length); }
	catch (const std::bad_alloc&) { return X_STATUS_NO_MEMORY; }

	return X_STATUS_SUCCESS;
}

CacheFile::CacheFile(uint32_t file_access, CacheEntry* entry, std::shared_ptr<CacheFileData> data) : File(file_access, entry), data_(std::move(data))
{
}

void CacheFile::Destroy()
{
	delete this;
}

X_STATUS CacheFile::ReadSync(std::span<uint8_t> buffer, size_t byte_offset, size_t* out_bytes_read)
{
	if (out_bytes_read) *out_bytes_read = 0;
	if (!data_) return X_STATUS_FILE_IS_A_DIRECTORY;
	if (!(file_access_ & (rex::filesystem::FileAccess::kGenericRead | rex::filesystem::FileAccess::kFileReadData))) {
		return X_STATUS_ACCESS_DENIED;
	}

	return data_->ReadSync(buffer.data(), buffer.size(), byte_offset, out_bytes_read);
}

X_STATUS CacheFile::WriteSync(std::span<const uint8_t> buffer, size_t byte_offset, size_t* out_bytes_written)
{
	if (out_bytes_written) *out_bytes_written = 0;
	if (!data_) return X_STATUS_FILE_IS_A_DIRECTORY;
	if (!(file_access_ & (rex::filesystem::FileAccess::kGenericWrite | rex::filesystem::FileAccess::kFileWriteData | rex::filesystem::FileAccess::kFileAppendData))) {
		return X_STATUS_ACCESS_DENIED;
	}

	return data_->WriteSync(buffer.data(), buffer.size(), byte_offset, out_bytes_written);
}

X_STATUS CacheFile::SetLength(size_t length)
{
	if (!data_) return X_STATUS_FILE_IS_A_DIRECTORY;
	if (!(file_access_ & (rex::filesystem::FileAccess::kGenericWrite | rex::filesystem::FileAccess::kFileWriteData))) {
		return X_STATUS_ACCESS_DENIED;
	}

	return data_->SetLength(length);
}

CacheEntry::CacheEntry(rex::filesystem::Device* device, Entry* parent, const std::string_view path) : Entry(device, parent, path)
{
}

CacheEntry* CacheEntry::Create(rex::filesystem::Device* device, Entry* parent, const std::string_view path, uint32_t attribute)
{
	auto entry = new CacheEntry(device, parent, path);

	entry->create_timestamp_ = 0;
	entry->access_timestamp_ = 0;
	entry->write_timestamp_ = 0;
	entry->attributes_ = attribute;
	if (!(attribute & rex::filesystem::kFileAttributeDirectory)) {
		entry->size_ = 0;
		entry->allocation_size_ = 0;

		entry->data_ = std::make_shared<CacheFileData>();
	}

	return entry;
}

X_STATUS CacheEntry::Open(uint32_t desired_access, rex::filesystem::File** out_file)
{
	if (is_read_only() && (desired_access & (rex::filesystem::FileAccess::kFileWriteData | rex::filesystem::FileAccess::kFileAppendData))) {
		return X_STATUS_ACCESS_DENIED;
	}

	*out_file = new CacheFile(desired_access, this, data_);

	return X_STATUS_SUCCESS;
}

void CacheEntry::update()
{
	if (!(attributes_ & rex::filesystem::kFileAttributeDirectory) && data_) {
		size_ = data_->GetSize();
		allocation_size_ = rex::round_up(size_, device()->bytes_per_sector());
	}
}

std::unique_ptr<rex::filesystem::Entry> CacheEntry::CreateEntryInternal(const std::string_view name, uint32_t attributes)
{
	return std::unique_ptr<Entry>(Create(device_, this,
		rex::string::utf8_join_guest_paths(path_, name), attributes));
}

bool CacheEntry::DeleteEntryInternal(rex::filesystem::Entry* entry)
{
	// Entry::Delete removes the child; open handles retain their shared data.
	(void)entry;
	return true;
}

CacheDevice::CacheDevice(const std::string_view mount_path) : Device(mount_path)
{
	// Convert `\\CACHE` into `CACHE`
	auto lastSlash = mount_path.find_last_of('\\');
	name_ = lastSlash == std::string::npos ? mount_path : mount_path.substr(lastSlash + 1);
}

bool CacheDevice::Initialize()
{
	auto root_entry = new CacheEntry(this, nullptr, "");
	root_entry->attributes_ = rex::filesystem::kFileAttributeDirectory;
	root_entry_ = std::unique_ptr<rex::filesystem::Entry>(root_entry);

	return true;
}

void CacheDevice::Dump(rex::string::StringBuffer* string_buffer)
{
	root_entry_->Dump(string_buffer, 0);
}

rex::filesystem::Entry* CacheDevice::ResolvePath(const std::string_view path)
{
	return root_entry_->ResolvePath(path);
}
