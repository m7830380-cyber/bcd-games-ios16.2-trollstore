// iOS 16.0-16.2 (TrollStore fork): libc++ symbols the app's native libraries
// import from /usr/lib/libc++.1.dylib that this system's libc++ does not have.
// A strongly bound symbol missing at launch stops dyld from starting the app,
// so they are defined here, in an object file of the app target: the static
// link binds every reference to these definitions and none to the dylib.
// (An archive member would not do: the linker only pulls one for a symbol
// nothing else defines, and libc++.tbd does define these.)
//
//   std::to_chars (floating point)    iOS 16.3   std::format, in DXMT
//   std::__libcpp_verbose_abort       iOS 16.3   libc++ assertions
//   std::pmr::memory_resource         iOS 17.0   vtable key function and typeinfo
//   pipe2 (C, below)                  newer      Wine's ntdll
#define _LIBCPP_DISABLE_AVAILABILITY 1

#include <charconv>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory_resource>
#include <system_error>

namespace {

template <typename T>
std::to_chars_result put(char *first, char *last, const char *fmt, int precision, T value) {
    char buf[512];
    int n = precision >= 0 ? snprintf(buf, sizeof(buf), fmt, precision, (long double)value)
                           : snprintf(buf, sizeof(buf), fmt, (long double)value);
    if (n < 0 || n >= (int)sizeof(buf) || n > last - first)
        return {last, std::errc::value_too_large};
    memcpy(first, buf, (size_t)n);
    return {first + n, std::errc{}};
}

// Round-trip digits for the type in %g layout (not the shortest form, but the same value).
template <typename T>
std::to_chars_result shortest(char *first, char *last, T value) {
    return put(first, last, "%.*Lg", sizeof(T) == sizeof(float) ? 9 : 17, value);
}

template <typename T>
std::to_chars_result formatted(char *first, char *last, T value, std::chars_format fmt, int precision) {
    const bool p = precision >= 0;
    switch (fmt) {
    case std::chars_format::scientific: return put(first, last, p ? "%.*Le" : "%Le", precision, value);
    case std::chars_format::fixed:      return put(first, last, p ? "%.*Lf" : "%Lf", precision, value);
    case std::chars_format::hex:        return put(first, last, p ? "%.*La" : "%La", precision, value);
    default:                            return p ? put(first, last, "%.*Lg", precision, value) : shortest(first, last, value);
    }
}

} // namespace

namespace std {
inline namespace __1 {

to_chars_result to_chars(char *f, char *l, float v) { return shortest(f, l, v); }
to_chars_result to_chars(char *f, char *l, double v) { return shortest(f, l, v); }
to_chars_result to_chars(char *f, char *l, long double v) { return shortest(f, l, v); }
to_chars_result to_chars(char *f, char *l, float v, chars_format c) { return formatted(f, l, v, c, -1); }
to_chars_result to_chars(char *f, char *l, double v, chars_format c) { return formatted(f, l, v, c, -1); }
to_chars_result to_chars(char *f, char *l, long double v, chars_format c) { return formatted(f, l, v, c, -1); }
to_chars_result to_chars(char *f, char *l, float v, chars_format c, int p) { return formatted(f, l, v, c, p); }
to_chars_result to_chars(char *f, char *l, double v, chars_format c, int p) { return formatted(f, l, v, c, p); }
to_chars_result to_chars(char *f, char *l, long double v, chars_format c, int p) { return formatted(f, l, v, c, p); }

// libc++ documents this function as one an application may replace.
void __libcpp_verbose_abort(const char *format, ...) noexcept {
    va_list ap;
    va_start(ap, format);
    vfprintf(stderr, format, ap);
    va_end(ap);
    fputc('\n', stderr);
    abort();
}

namespace pmr {
// The key function: defining it here also emits the vtable and typeinfo.
memory_resource::~memory_resource() = default;
} // namespace pmr

} // namespace __1
} // namespace std

// pipe2 (libSystem, newer than iOS 16): BCD's ntdll calls it without a check
// (process_ios.c), and as a weak import it is NULL on iOS 16. Defined here the
// same way, so every call lands in this one, which uses the system's where
// there is one and pipe + fcntl where there is not.
#include <cerrno>
#include <dlfcn.h>
#include <fcntl.h>
// Not <unistd.h>: its pipe2 carries the SDK's availability (a weak import below
// iOS 17), which must not meet this definition.
extern "C" int pipe(int fd[2]);

extern "C" int pipe2(int fd[2], int flags) {
    using fn = int (*)(int *, int);
    static fn real = [] {
        void *h = dlopen("/usr/lib/libSystem.B.dylib", RTLD_LAZY | RTLD_NOLOAD);
        return h ? reinterpret_cast<fn>(dlsym(h, "pipe2")) : nullptr;   // the system's, not this one
    }();
    if (real) return real(fd, flags);
    if (flags & ~(O_CLOEXEC | O_NONBLOCK)) { errno = EINVAL; return -1; }
    if (pipe(fd)) return -1;
    for (int i = 0; i < 2; i++) {
        if (flags & O_CLOEXEC) fcntl(fd[i], F_SETFD, FD_CLOEXEC);
        if (flags & O_NONBLOCK) fcntl(fd[i], F_SETFL, fcntl(fd[i], F_GETFL) | O_NONBLOCK);
    }
    return 0;
}
