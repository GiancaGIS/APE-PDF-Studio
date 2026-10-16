#include "sandbox.h"
#include <cstdio>

int wmain(int argc, wchar_t** argv) {
    // host work timeout-ms stdout-budget read-count [read-dir...] executable [args...]
    if (argc < 7) return 2;
    try {
        unsigned long count = std::stoul(argv[4]);
        if (count > 8 || argc < static_cast<int>(count)+6) return 2;
        std::vector<std::wstring> code, arguments;
        for (unsigned long i = 0; i < count; ++i) code.emplace_back(argv[5+i]);
        int executable = 5 + static_cast<int>(count);
        for (int i = executable+1; i < argc; ++i) arguments.emplace_back(argv[i]);
        return ape_sandbox::run(argv[executable], arguments, argv[1], code,
            std::stoul(argv[2]), std::stoull(argv[3]));
    } catch (...) { return 2; }
}
