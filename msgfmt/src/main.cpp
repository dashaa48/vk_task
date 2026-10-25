// Command line: `msgfmt` (parse stdin) and `msgfmt escape`.
#include <cstdio>
#include <cstring>
#include <string>

#include "msgfmt/msgfmt.hpp"

namespace {

std::string read_all(std::FILE* f) {
    std::string data;
    char buf[65536];
    std::size_t got;
    while ((got = std::fread(buf, 1, sizeof buf, f)) > 0) data.append(buf, got);
    return data;
}

void write_all(const std::string& s) {
    if (!s.empty()) std::fwrite(s.data(), 1, s.size(), stdout);
}

}  // namespace

int main(int argc, char** argv) {
    bool do_escape = false;
    if (argc == 2 && std::strcmp(argv[1], "escape") == 0) {
        do_escape = true;
    } else if (argc != 1) {
        std::fputs("usage: msgfmt [escape] < input\n", stderr);
        return 1;
    }

    const std::string input = read_all(stdin);

    if (do_escape) {
        const auto escaped = msgfmt::escape(input);
        if (!escaped) {
            write_all("{\"error\": \"invalid_utf8\"}\n");
            return 2;
        }
        write_all(*escaped);  // no trailing newline: the text is transformed as is
        return 0;
    }

    const auto result = msgfmt::parse(input);
    if (!result) {
        write_all("{\"error\": \"invalid_utf8\"}\n");
        return 2;
    }
    write_all(msgfmt::to_json(*result) + "\n");
    return 0;
}
