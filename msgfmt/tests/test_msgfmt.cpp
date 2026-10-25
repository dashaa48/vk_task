// Self-contained unit tests (no framework): the table of Appendix A,
// UTF-16 offsets, UTF-8 validation, escaping, JSON output and a
// deterministic property test over random marker soup.
#include <cstdint>
#include <iostream>
#include <random>
#include <string>
#include <vector>

#include "msgfmt/msgfmt.hpp"

namespace {

int g_failures = 0;
int g_checks = 0;

std::string describe(const msgfmt::ParseResult& r) {
    std::string s;
    for (const auto& x : r.ranges) {
        if (!s.empty()) s += "; ";
        s += msgfmt::style_name(x.style);
        s += "(" + std::to_string(x.offset) + ", " + std::to_string(x.length);
        if (x.style == msgfmt::Style::Link) s += ", " + x.url;
        s += ")";
    }
    return s;
}

void expect_parse(const std::string& input, const std::string& text, const std::string& ranges) {
    ++g_checks;
    const auto r = msgfmt::parse(input);
    if (!r) {
        ++g_failures;
        std::cerr << "FAIL parse(" << input << "): unexpected invalid_utf8\n";
        return;
    }
    if (r->text != text || describe(*r) != ranges) {
        ++g_failures;
        std::cerr << "FAIL parse(" << input << ")\n  text:   [" << r->text << "] expected [" << text
                  << "]\n  ranges: [" << describe(*r) << "] expected [" << ranges << "]\n";
    }
}

void expect_eq(const std::string& got, const std::string& want, const char* what) {
    ++g_checks;
    if (got != want) {
        ++g_failures;
        std::cerr << "FAIL " << what << ": got [" << got << "] expected [" << want << "]\n";
    }
}

void expect_invalid(const std::string& bytes, const char* what) {
    ++g_checks;
    if (msgfmt::parse(bytes) || msgfmt::escape(bytes) || msgfmt::is_valid_utf8(bytes)) {
        ++g_failures;
        std::cerr << "FAIL invalid utf-8 accepted: " << what << "\n";
    }
}

void expect_valid(const std::string& bytes, const char* what) {
    ++g_checks;
    if (!msgfmt::parse(bytes) || !msgfmt::is_valid_utf8(bytes)) {
        ++g_failures;
        std::cerr << "FAIL valid utf-8 rejected: " << what << "\n";
    }
}

const std::string kRocket = "\xF0\x9F\x9A\x80";  // U+1F680, outside the BMP

void appendix_a() {
    expect_parse("**важно**", "важно", "bold(0, 5)");                                       // 1
    expect_parse("Привет, **мир**!", "Привет, мир!", "bold(8, 3)");                         // 2
    expect_parse("_курсив_", "курсив", "italic(0, 6)");                                     // 3
    expect_parse("a_b_c", "a_b_c", "");                                                     // 4
    expect_parse("**не закрыто", "**не закрыто", "");                                       // 5
    expect_parse("2 \\* 3", "2 * 3", "");                                                   // 6
    expect_parse("`чат * список`", "чат * список", "code(0, 12)");                          // 7
    expect_parse("[документация](https://docs.github.com)", "документация",
                 "link(0, 12, https://docs.github.com)");                                   // 8
    expect_parse("**жирный _и_ курсив**", "жирный и курсив", "bold(0, 15); italic(7, 1)");  // 9
    expect_parse(kRocket + " **ок**", kRocket + " ок", "bold(3, 2)");                       // 10
    expect_parse("**" + kRocket + " ок**", kRocket + " ок", "bold(0, 5)");                  // 11
    expect_parse("[текст]()", "[текст]()", "");                                             // 12
    expect_parse("\\\\", "\\", "");                                                         // 13
    expect_parse("", "", "");                                                               // 14
    expect_parse("_ е_", "_ е_", "");                                                       // 15
}

void rules() {
    // Rule 3/5: italic boundaries.
    expect_parse("**жирный _и курсив_**", "жирный и курсив", "bold(0, 15); italic(7, 8)");
    expect_parse("_a_b", "_a_b", "");               // closing '_' followed by a letter
    expect_parse("_a_ b", "a b", "italic(0, 1)");
    expect_parse("x (_a_)", "x (a)", "italic(3, 1)");
    expect_parse("_a _", "_a _", "");               // closer preceded by a space
    expect_parse("_Привет_ мир", "Привет мир", "italic(0, 6)");
    expect_parse("слово_и_слово", "слово_и_слово", "");
    expect_parse("1_2_3", "1_2_3", "");
    // Rule 4.
    expect_parse("2 ** 3", "2 ** 3", "");
    expect_parse("** 3", "** 3", "");
    expect_parse("**a **", "**a **", "");
    expect_parse("****", "****", "");
    expect_parse("`` `", "`` `", "");
    // Rule 5: nearest closer.
    expect_parse("**a** b **c**", "a b c", "bold(0, 1); bold(4, 1)");
    // Rule 7: code is verbatim.
    expect_parse("`**a** \\* _b_`", "**a** \\* _b_", "code(0, 12)");
    expect_parse("**x `**` y**", "x ** y", "bold(0, 6); code(2, 2)");
    expect_parse("`a ` b`", "a ` b", "code(0, 5)");   // closer must follow non-space
    // Rule 8: links.
    expect_parse("[a](b c)", "[a](b c)", "");
    expect_parse("[](u)", "[](u)", "");
    expect_parse("[a]", "[a]", "");
    expect_parse("[a] (u)", "[a] (u)", "");
    expect_parse("[a](u", "[a](u", "");
    expect_parse("[a](u)b", "ab", "link(0, 1, u)");
    expect_parse("[**a**](u)", "a", "link(0, 1, u); bold(0, 1)");
    expect_parse("[a [b](u)](v)", "a [b](v)", "link(0, 4, u)");
    expect_parse("[a\\]b](u)", "a]b", "link(0, 3, u)");
    // Rule 9: escaping.
    expect_parse("\\*\\_\\`\\[\\]\\(\\)\\\\", "*_`[]()\\", "");
    expect_parse("\\a", "\\a", "");
    expect_parse("abc\\", "abc\\", "");
    expect_parse("\\**a**", "**a**", "");
    expect_parse("**a\\***", "a*", "bold(0, 2)");
    // Rule 10: nesting.
    expect_parse("**a **b** c**", "a **b c**", "bold(0, 5)");
    expect_parse("**a** **b**", "a b", "bold(0, 1); bold(2, 1)");
    expect_parse("**_a_**", "a", "bold(0, 1); italic(0, 1)");
    expect_parse("_**a**_", "a", "italic(0, 1); bold(0, 1)");
    expect_parse("_a _b_ c_", "a _b c_", "italic(0, 4)");
    // No partial intersections: the unmatched opener degrades to text.
    expect_parse("**a _b** c_", "**a b** c", "italic(4, 5)");
    // Rule 12: ordering with equal intervals.
    expect_parse("**_a_**", "a", "bold(0, 1); italic(0, 1)");
    expect_parse("__a__", "_a_", "italic(0, 2)");
    expect_parse("a **b** _c_ `d`", "a b c d", "bold(2, 1); italic(4, 1); code(6, 1)");
}

void utf16_positions() {
    // Combining sequence vs precomposed letter: different strings and offsets.
    expect_parse("\xD0\xB9 **a**", "\xD0\xB9 a", "bold(2, 1)");                  // U+0439
    expect_parse("\xD0\xB8\xCC\x86 **a**", "\xD0\xB8\xCC\x86 a", "bold(3, 1)");  // U+0438 U+0306
    expect_parse(kRocket + kRocket + "_x_", kRocket + kRocket + "x", "italic(4, 1)");
    expect_parse("**" + kRocket + "**", kRocket, "bold(0, 2)");
    expect_parse("`" + kRocket + "`", kRocket, "code(0, 2)");
    // BMP characters beyond U+07FF take one unit (three UTF-8 bytes).
    expect_parse("\xE4\xBD\xA0\xE5\xA5\xBD **a**", "\xE4\xBD\xA0\xE5\xA5\xBD a", "bold(3, 1)");
    // Newlines and control characters are ordinary text.
    expect_parse("a\n**b**\tc", "a\nb\tc", "bold(2, 1)");
    expect_parse(std::string("a\0b", 3), std::string("a\0b", 3), "");
}

void utf8_validation() {
    expect_valid("", "empty");
    expect_valid("\x7F", "DEL");
    expect_valid("\xC2\x80", "U+0080");
    expect_valid("\xDF\xBF", "U+07FF");
    expect_valid("\xE0\xA0\x80", "U+0800");
    expect_valid("\xED\x9F\xBF", "U+D7FF");
    expect_valid("\xEE\x80\x80", "U+E000");
    expect_valid("\xEF\xBF\xBF", "U+FFFF");
    expect_valid("\xF0\x90\x80\x80", "U+10000");
    expect_valid("\xF4\x8F\xBF\xBF", "U+10FFFF");
    expect_invalid("\x80", "lone continuation byte");
    expect_invalid("\xBF", "lone continuation byte (BF)");
    expect_invalid("\xC0\x80", "overlong NUL");
    expect_invalid("\xC1\xBF", "overlong 2-byte");
    expect_invalid("\xE0\x80\x80", "overlong 3-byte");
    expect_invalid("\xF0\x80\x80\x80", "overlong 4-byte");
    expect_invalid("\xED\xA0\x80", "surrogate U+D800");
    expect_invalid("\xED\xBF\xBF", "surrogate U+DFFF");
    expect_invalid("\xF4\x90\x80\x80", "above U+10FFFF");
    expect_invalid("\xF5\x80\x80\x80", "F5 lead byte");
    expect_invalid("\xFF", "FF byte");
    expect_invalid("\xC3", "truncated 2-byte at end");
    expect_invalid("\xE2\x82", "truncated 3-byte at end");
    expect_invalid("\xF0\x9F\x9A", "truncated 4-byte at end");
    expect_invalid("a\xC3(b", "bad continuation");
    expect_invalid("**ok** \xFF", "bad byte after markup");
}

void escaping() {
    expect_eq(*msgfmt::escape("2 * 3"), "2 \\* 3", "escape basic");
    expect_eq(*msgfmt::escape("*_`[]()\\"), "\\*\\_\\`\\[\\]\\(\\)\\\\", "escape all");
    expect_eq(*msgfmt::escape(""), "", "escape empty");
    expect_eq(*msgfmt::escape("привет " + kRocket), "привет " + kRocket, "escape keeps non-ASCII");
    // escape followed by parse is the identity on the text.
    const std::string raw = "a_b *c* `d` [e](f) \\ g";
    const auto round = msgfmt::parse(*msgfmt::escape(raw));
    ++g_checks;
    if (!round || round->text != raw || !round->ranges.empty()) {
        ++g_failures;
        std::cerr << "FAIL escape/parse round trip\n";
    }
}

void json_output() {
    const auto r = msgfmt::parse("Привет, **мир**! \"q\" \\\\ \n");
    expect_eq(msgfmt::to_json(*r),
              "{\"text\": \"Привет, мир! \\\"q\\\" \\\\ \\n\", \"ranges\": "
              "[{\"style\": \"bold\", \"offset\": 8, \"length\": 3, \"url\": \"\"}]}",
              "json bold + escapes");
    expect_eq(msgfmt::to_json(*msgfmt::parse("")), "{\"text\": \"\", \"ranges\": []}", "json empty");
    expect_eq(msgfmt::to_json(*msgfmt::parse("[a](http://x/?q=\"1\")")),
              "{\"text\": \"a\", \"ranges\": [{\"style\": \"link\", \"offset\": 0, "
              "\"length\": 1, \"url\": \"http://x/?q=\\\"1\\\"\"}]}",
              "json link url escaping");
    expect_eq(msgfmt::to_json(*msgfmt::parse(std::string("\x01", 1))),
              "{\"text\": \"\\u0001\", \"ranges\": []}", "json control chars");
}

std::size_t utf16_len(const std::string& utf8) {
    std::size_t n = 0;
    for (unsigned char c : utf8) {
        if ((c & 0xC0) == 0x80) continue;      // continuation byte
        n += (c >= 0xF0) ? 2 : 1;               // 4-byte lead => surrogate pair
    }
    return n;
}

// Random strings built from markers: invariants must hold and (under the
// sanitizers) nothing may crash or read out of bounds.
void property_test() {
    static const char* atoms[] = {"*", "**", "_", "`", "[", "]", "(", ")", "\\", "a", "b", " ",
                                  "\xD0\xB0", "\xF0\x9F\x9A\x80", "http", "/", "1", "\n"};
    std::mt19937 rng(12345);
    for (int iter = 0; iter < 20000; ++iter) {
        std::string in;
        const int len = static_cast<int>(rng() % 24);
        for (int i = 0; i < len; ++i) in += atoms[rng() % (sizeof atoms / sizeof atoms[0])];

        const auto r = msgfmt::parse(in);
        ++g_checks;
        bool ok = r.has_value();
        if (ok) {
            const std::size_t total = utf16_len(r->text);
            for (std::size_t i = 0; ok && i < r->ranges.size(); ++i) {
                const auto& a = r->ranges[i];
                if (a.length == 0 || a.offset + a.length > total) ok = false;
                if ((a.style == msgfmt::Style::Link) == a.url.empty()) ok = false;
                if (i > 0) {
                    const auto& p = r->ranges[i - 1];
                    if (a.offset < p.offset) ok = false;
                    if (a.offset == p.offset && a.length > p.length) ok = false;
                }
                for (std::size_t j = i + 1; ok && j < r->ranges.size(); ++j) {
                    const auto& b = r->ranges[j];
                    const bool disjoint = b.offset >= a.offset + a.length || a.offset >= b.offset + b.length;
                    const bool a_in_b = a.offset >= b.offset && a.offset + a.length <= b.offset + b.length;
                    const bool b_in_a = b.offset >= a.offset && b.offset + b.length <= a.offset + a.length;
                    if (!disjoint && !a_in_b && !b_in_a) ok = false;  // partial intersection
                }
            }
            // Determinism and the escape/parse identity.
            const auto again = msgfmt::parse(in);
            if (!again || msgfmt::to_json(*again) != msgfmt::to_json(*r)) ok = false;
            const auto round = msgfmt::parse(*msgfmt::escape(in));
            if (!round || round->text != in || !round->ranges.empty()) ok = false;
        }
        if (!ok) {
            ++g_failures;
            std::cerr << "FAIL property for input [" << in << "]\n";
            if (g_failures > 10) return;
        }
    }
}

void pathological_inputs() {
    // Many unmatched markers must not blow up (quadratic at worst).
    std::string s;
    for (int i = 0; i < 3000; ++i) s += "**a ";
    ++g_checks;
    if (!msgfmt::parse(s)) ++g_failures;
    std::string t;
    for (int i = 0; i < 3000; ++i) t += "[a";
    ++g_checks;
    if (!msgfmt::parse(t)) ++g_failures;
    std::string u(100000, '`');
    ++g_checks;
    if (!msgfmt::parse(u)) ++g_failures;
}

}  // namespace

int main() {
    appendix_a();
    rules();
    utf16_positions();
    utf8_validation();
    escaping();
    json_output();
    property_test();
    pathological_inputs();
    std::cout << g_checks << " checks, " << g_failures << " failures\n";
    return g_failures == 0 ? 0 : 1;
}
