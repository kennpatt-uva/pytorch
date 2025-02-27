load("@rules_cc//cc:defs.bzl", "cc_library")
load("//c10/macros:cmake_configure_file.bzl", "cmake_configure_file")

# Rules implementation for the Bazel build system. Since the common
# build structure aims to replicate Bazel as much as possible, most of
# the rules simply forward to the Bazel definitions.
rules = struct(
    cc_library = cc_library,
    cmake_configure_file = cmake_configure_file,
    select = select,
)
