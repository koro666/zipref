#!/usr/bin/env python3.10
import os
import sys
import typing
import stat
import struct
import time
import zlib
import fcntl

# https://blog.yaakov.online/zip64-go-big-or-go-home/

ZipFileHeader = struct.Struct('<IHHHHHIIIHH')
Zip64FileHeaderExtraField = struct.Struct('<HHQQ')
ZipCentralDirectoryFileHeader = struct.Struct('<IHHHHHHIIIHHHHHII')
Zip64CentralDirectoryFileHeaderExtraField = struct.Struct('<HHQQQ')
Zip64EndOfCentralDirectoryRecord = struct.Struct('<IQHHIIQQQQ')
Zip64EndOfCentralDirectoryLocator = struct.Struct('<IIQI')
ZipEndOfCentralDirectoryRecord = struct.Struct('<IHHHHIIH')

FileCloneRange = struct.Struct('@qQQQ')

def chunk_iterator(fd: int, position: int, length: int, chunk_size: int = 63356) -> typing.Iterable[bytes]:
	os.lseek(fd, position, os.SEEK_SET)
	while length:
		data = os.read(fd, min(chunk_size, length))
		if not data:
			continue
		length = length - len(data)
		yield data

def progress(it: typing.Iterable[bytes], each: int = 1048576 * 32) -> typing.Iterable[bytes]:
	position = 0
	written_at = 0
	for data in it:
		position = position + len(data)
		if (position - written_at) >= each:
			print('.', end='', flush=True)
			written_at = position
		yield data

def compute_crc32(it: typing.Iterable[bytes]) -> int:
	crc = 0
	for data in it:
		crc = zlib.crc32(data, crc)
	return crc

def get_dos_date_time(t: float) -> tuple[int, int]:
	tm = time.localtime(t)

	d = ((tm.tm_year - 1980) << 9) | (tm.tm_mon << 5) | tm.tm_mday
	t = (tm.tm_hour << 11) | (tm.tm_min << 5) | (tm.tm_sec >> 1)

	return (d, t)

def make_file_header(name: str, st: os.stat_result, crc: int) -> bytes:
	dos_tm = get_dos_date_time(st.st_mtime)
	bname = name.encode('utf-8', errors='surrogateescape')
	extra = Zip64FileHeaderExtraField.pack(1, 16, st.st_size, st.st_size)
	fixed_header = ZipFileHeader.pack(0x04034b50, 45, 1 << 11, 0, dos_tm[1], dos_tm[0], crc, 0xffffffff, 0xffffffff, len(bname), len(extra))
	return fixed_header + bname + extra

def make_central_header(name: str, st: os.stat_result, crc: int, offset: int) -> bytes:
	dos_tm = get_dos_date_time(st.st_mtime)
	bname = name.encode('utf-8', errors='surrogateescape')
	extra = Zip64CentralDirectoryFileHeaderExtraField.pack(1, 24, st.st_size, st.st_size, offset)
	fixed_header = ZipCentralDirectoryFileHeader.pack(0x02014b50, 45, 45, 1 << 11, 0, dos_tm[1], dos_tm[0], crc, 0xffffffff, 0xffffffff, len(bname), len(extra), 0, 0, 0, 0, 0xffffffff)
	return fixed_header + bname + extra

def clone_range(src_fd: int, src_offset: int, src_length: int, dst_fd: int,  dst_offset: int) -> None:
	data = FileCloneRange.pack(src_fd, src_offset, src_length, dst_offset)
	fcntl.ioctl(dst_fd, 0x4020940d, data, False)

def write_all(fd:int, it: typing.Iterable[bytes]) -> None:
	for data in it:
		os.write(fd, data)

def execute(fd: int, paths: typing.Iterable[str], alignment: int) -> None:
	print('[alignment=0x%08x]' % alignment)
	datas: list[tuple[str, os.stat_result, int, int]] = []
	for path in paths:
		st = os.lstat(path)
		if not stat.S_ISREG(st.st_mode):
			continue

		print('%s...' % path, end='', flush=True)

		fd2 = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
		try:
			crc = compute_crc32(progress(chunk_iterator(fd2, 0, st.st_size)))
			header = make_file_header(path, st, crc)

			offset = os.lseek(fd, 0, os.SEEK_END)
			offset = (int((offset + len(header) + alignment - 1) / alignment) * alignment) - len(header)
			os.lseek(fd, offset, os.SEEK_SET)

			os.write(fd, header)
			print('[crc=0x%08x, header=0x%016x]...' % (crc, offset), end='', flush=True)

			try:
				clone_range(fd2, 0, st.st_size, fd, offset + len(header))
				cloned = True
			except OSError as e:
				print('[error=%d]...' % e.errno, end='', flush=True)
				write_all(fd, progress(chunk_iterator(fd2, 0, st.st_size)))
				cloned = False
		finally:
			os.close(fd2)

		print('cloned!' if cloned else 'copied!')

		datas.append((path, st, crc, offset))

	central_start = os.lseek(fd, 0, os.SEEK_END)
	for path, st, crc, offset in datas:
		header = make_central_header(path, st, crc, offset)
		os.write(fd, header)
	central_end = os.lseek(fd, 0, os.SEEK_CUR)

	data = Zip64EndOfCentralDirectoryRecord.pack(0x06064b50, 44, 45, 45, 0, 0, len(datas), len(datas), central_end - central_start, central_start)
	os.write(fd, data)

	data = Zip64EndOfCentralDirectoryLocator.pack(0x07064b50, 0, central_end, 1)
	os.write(fd, data)

	data = ZipEndOfCentralDirectoryRecord.pack(0x06054b50, 0, 0, 0xffff, 0xffff, 0xffffffff, 0xffffffff, 0)
	os.write(fd, data)

def get_paths(paths: typing.Iterable[str]) -> typing.Iterable[str]:
	for path in paths:
		if path.startswith('@'):
			with open(path[1:], 'r') as fp:
				yield from get_paths(map(str.rstrip, fp))
		else:
			yield os.path.normpath(path)

def main(args: list[str]) -> None:
	if not len(args):
		return

	fd = os.open(args[0], os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC, 0o666)
	try:
		execute(fd, get_paths(args[1:]), os.fstatvfs(fd).f_bsize)
	finally:
		os.close(fd)

if __name__ == '__main__':
	main(sys.argv[1:])
