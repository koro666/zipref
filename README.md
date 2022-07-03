zipref
======

`zipref` is a script that generates (uncompressed) ZIP files on copy-on-write filesystems by ref-linking the contents of the source files inside the archive.

It does so by (ab)using the fact that the ZIP format allows padding between entries to carefully align the file header for each entry so that the data starts at an offset which is a multiple of block size. This allows cloning the data from the source file directly.

The resulting archive is a proper ZIP64 archive and should barely take up any extra space. It has been tested to extract properly in WinRAR, `7z` and `unzip`.

Note that the process is not instantaneous: the data still has to be read in order to compute the CRC32 for each entry. Additionally, if the clone `ioctl` fails, the script will revert to copying the data manually.

Usage
-----

The script takes the name of the output archive as first argument, as well as the name of files to add as additional arguments. It's possible to use `@` to specify a file containing a list of files, and to use `-` to read said list from standard input.

The output file must not exist already, and the input files are added **as-is** in the specified order, using their paths verbatim as they are specified.

Some examples:
```bash
# Specify file list on the command line
zipref output.zip *.mp3

# Specify file list from an input file
zipref output.zip @files.txt

# Specify file list from standard input
find -type f | zipref output.zip
```

Additionally, it is possible to generate a reproducible archive using the [`SOURCE_DATE_EPOCH`](https://reproducible-builds.org/specs/source-date-epoch/) environment variable:

```bash
# Generate reproducible archive
export TZ=UTC
export SOURCE_DATE_EPOCH=1600000000 # or $(stat -c %Y reference_file)
find -type f | sort -f | zipref output.zip
```

References
----------

- [ZIP64 - Go Big Or Go Home](https://blog.yaakov.online/zip64-go-big-or-go-home/)
