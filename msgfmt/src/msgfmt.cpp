#include "msgfmt/msgfmt.hpp"

#include <algorithm>
#include <set>
#include <utility>

namespace msgfmt {

namespace {

// ---------------------------------------------------------------- UTF-8 ---

bool decode_utf8(const std::string& in, std::u32string& out) {
    out.clear();
    out.reserve(in.size());
    const std::size_t n = in.size();
    std::size_t i = 0;
    while (i < n) {
        const unsigned char b0 = static_cast<unsigned char>(in[i]);
        if (b0 < 0x80) {
            out.push_back(b0);
            ++i;
            continue;
        }
        std::size_t extra;
        char32_t cp;
        char32_t min_cp;
        if (b0 >= 0xC2 && b0 <= 0xDF) {
            extra = 1; cp = b0 & 0x1Fu; min_cp = 0x80;
        } else if (b0 >= 0xE0 && b0 <= 0xEF) {
            extra = 2; cp = b0 & 0x0Fu; min_cp = 0x800;
        } else if (b0 >= 0xF0 && b0 <= 0xF4) {
            extra = 3; cp = b0 & 0x07u; min_cp = 0x10000;
        } else {
            return false;  // stray continuation byte, C0/C1, F5..FF
        }
        if (i + extra >= n) return false;  // truncated sequence
        for (std::size_t k = 1; k <= extra; ++k) {
            const unsigned char b = static_cast<unsigned char>(in[i + k]);
            if ((b & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (b & 0x3Fu);
        }
        if (cp < min_cp) return false;                    // overlong
        if (cp >= 0xD800 && cp <= 0xDFFF) return false;   // surrogate
        if (cp > 0x10FFFF) return false;
        out.push_back(cp);
        i += extra + 1;
    }
    return true;
}

void append_utf8(std::string& out, char32_t cp) {
    if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
}

std::string encode_utf8(const char32_t* first, const char32_t* last) {
    std::string s;
    for (; first != last; ++first) append_utf8(s, *first);
    return s;
}

// ------------------------------------------------- character classes ------

bool is_space(char32_t c) {
    switch (c) {
        case 0x09: case 0x0A: case 0x0B: case 0x0C: case 0x0D: case 0x20:
        case 0x85: case 0xA0: case 0x1680: case 0x2028: case 0x2029:
        case 0x202F: case 0x205F: case 0x3000:
            return true;
        default:
            return c >= 0x2000 && c <= 0x200A;
    }
}

// Letters and digits. Locale-independent table of the main Unicode blocks
// (an approximation of general categories L* and Nd; see README).
bool is_letter_or_digit(char32_t c) {
    struct Span { char32_t lo, hi; };
    static const Span spans[] = {
        {0x30, 0x39}, {0x41, 0x5A}, {0x61, 0x7A}, {0xAA, 0xAA}, {0xB5, 0xB5},
        {0xBA, 0xBA}, {0xC0, 0xD6}, {0xD8, 0xF6}, {0xF8, 0x2C1},
        {0x370, 0x373}, {0x376, 0x377}, {0x37B, 0x37D}, {0x386, 0x386},
        {0x388, 0x3FF}, {0x400, 0x481}, {0x48A, 0x52F}, {0x531, 0x556},
        {0x561, 0x587}, {0x5D0, 0x5EA}, {0x620, 0x64A}, {0x660, 0x669},
        {0x66E, 0x6D3}, {0x6F0, 0x6F9}, {0x904, 0x939}, {0x966, 0x96F},
        {0xE01, 0xE30}, {0xE50, 0xE59}, {0x10A0, 0x10FF}, {0x1E00, 0x1FFF},
        {0x3041, 0x3096}, {0x30A1, 0x30FA}, {0x3400, 0x4DBF}, {0x4E00, 0x9FFF},
        {0xAC00, 0xD7A3}, {0xFF10, 0xFF19}, {0xFF21, 0xFF3A}, {0xFF41, 0xFF5A},
        {0x20000, 0x2FA1F},
    };
    for (const Span& s : spans) {
        if (c >= s.lo && c <= s.hi) return true;
    }
    return false;
}

bool is_service(char32_t c) {
    switch (c) {
        case U'*': case U'_': case U'`': case U'[': case U']':
        case U'(': case U')': case U'\\':
            return true;
        default:
            return false;
    }
}

std::size_t utf16_units(char32_t c) { return c >= 0x10000 ? 2 : 1; }

// ---------------------------------------------------------------- parser ---

enum : unsigned { kBold = 1, kItalic = 2, kLink = 4 };
enum class Closer { None, Bold, Italic, LinkText };

class Parser {
public:
    explicit Parser(const std::u32string& src) : s_(src), n_(src.size()) {
        // Precomputed helpers for the code style (O(1) closer lookup):
        // next_code_closer_[i] = smallest q >= i with s[q] == '`' and a
        // non-whitespace character before it (n_ if there is none).
        next_code_closer_.assign(n_ + 2, n_);
        for (std::size_t i = n_; i-- > 0;) {
            next_code_closer_[i] = next_code_closer_[i + 1];
            if (s_[i] == U'`' && i > 0 && !is_space(s_[i - 1])) next_code_closer_[i] = i;
        }
        bracket_prefix_.assign(n_ + 1, 0);
        for (std::size_t i = 0; i < n_; ++i) {
            bracket_prefix_[i + 1] = bracket_prefix_[i] + (s_[i] == U']' ? 1 : 0);
        }
    }

    ParseResult run() {
        std::size_t pos = 0;
        std::string unused;
        seq(pos, 0, Closer::None, 0, unused);

        // Rule 12: offset asc, length desc, then the earlier opener first.
        std::stable_sort(ranges_.begin(), ranges_.end(), [](const Pending& a, const Pending& b) {
            if (a.offset != b.offset) return a.offset < b.offset;
            if (a.length != b.length) return a.length > b.length;
            return a.order < b.order;
        });

        ParseResult res;
        res.text = encode_utf8(out_.data(), out_.data() + out_.size());
        res.ranges.reserve(ranges_.size());
        for (Pending& p : ranges_) {
            res.ranges.push_back(Range{p.style, p.offset, p.length, std::move(p.url)});
        }
        return res;
    }

private:
    struct Pending {
        Style style;
        std::size_t offset;
        std::size_t length;
        std::string url;
        std::size_t order;  // position of the opening marker in the input
    };

    void emit(char32_t c) {
        out_.push_back(c);
        units_ += utf16_units(c);
    }

    bool next_is_non_space(std::size_t p) const { return p < n_ && !is_space(s_[p]); }
    bool prev_is_non_space(std::size_t p) const { return p > 0 && !is_space(s_[p - 1]); }
    bool word_boundary_before(std::size_t p) const {
        return p == 0 || !is_letter_or_digit(s_[p - 1]);
    }
    bool is_bold_marker(std::size_t p) const {
        return p + 1 < n_ && s_[p] == U'*' && s_[p + 1] == U'*';
    }

    // Tries to read `(url)` right after the `]` at position `p`.
    bool link_target(std::size_t p, std::size_t& after, std::string& url) const {
        const std::size_t open_paren = p + 1;
        if (open_paren >= n_ || s_[open_paren] != U'(') return false;
        std::size_t q = open_paren + 1;
        while (q < n_ && s_[q] != U')' && !is_space(s_[q])) ++q;
        if (q >= n_ || s_[q] != U')' || q == open_paren + 1) return false;
        url = encode_utf8(s_.data() + open_paren + 1, s_.data() + q);
        after = q + 1;
        return true;
    }

    // Parses the content of an opened style (or the whole text for Closer::None).
    // Returns true when the closing marker was found (pos moves past it).
    bool seq(std::size_t& pos, unsigned mask, Closer closer, std::size_t content_start,
             std::string& url) {
        while (pos < n_) {
            const char32_t c = s_[pos];

            // Rule 9: escaping.
            if (c == U'\\' && pos + 1 < n_ && is_service(s_[pos + 1])) {
                emit(s_[pos + 1]);
                pos += 2;
                continue;
            }

            // Closing marker of the innermost open style (rules 3, 5, 8).
            if (closer == Closer::Bold) {
                if (is_bold_marker(pos) && pos > content_start && prev_is_non_space(pos)) {
                    pos += 2;
                    return true;
                }
            } else if (closer == Closer::Italic) {
                if (c == U'_' && pos > content_start && prev_is_non_space(pos) &&
                    (pos + 1 >= n_ || !is_letter_or_digit(s_[pos + 1]))) {
                    pos += 1;
                    return true;
                }
            } else if (closer == Closer::LinkText) {
                if (c == U']') {
                    std::size_t after = 0;
                    if (pos > content_start && link_target(pos, after, url)) {
                        pos = after;
                        return true;
                    }
                    return false;  // link text cannot contain ']'
                }
            }
            // A link text that encloses this style cannot contain ']' either.
            if (c == U']' && (mask & kLink) != 0) return false;

            if (c == U'*' && is_bold_marker(pos)) {
                std::size_t np = 0;
                if ((mask & kBold) == 0 && next_is_non_space(pos + 2) &&
                    open(Style::Bold, pos, 2, mask, np)) {
                    pos = np;
                } else {
                    emit(U'*');
                    emit(U'*');
                    pos += 2;
                }
                continue;
            }
            if (c == U'_') {
                std::size_t np = 0;
                if ((mask & kItalic) == 0 && word_boundary_before(pos) &&
                    next_is_non_space(pos + 1) && open(Style::Italic, pos, 1, mask, np)) {
                    pos = np;
                } else {
                    emit(c);
                    pos += 1;
                }
                continue;
            }
            if (c == U'[') {
                std::size_t np = 0;
                if ((mask & kLink) == 0 && next_is_non_space(pos + 1) &&
                    open(Style::Link, pos, 1, mask, np)) {
                    pos = np;
                } else {
                    emit(c);
                    pos += 1;
                }
                continue;
            }
            if (c == U'`') {
                std::size_t np = 0;
                if (next_is_non_space(pos + 1) && open_code(pos, mask, np)) {
                    pos = np;
                } else {
                    emit(c);
                    pos += 1;
                }
                continue;
            }
            emit(c);
            ++pos;
        }
        return closer == Closer::None;
    }

    // Opens bold / italic / link at `pos`. On failure (no closer) all output
    // produced by the attempt is rolled back and the caller prints literals.
    bool open(Style style, std::size_t pos, std::size_t marker_len, unsigned mask,
              std::size_t& new_pos) {
        const std::pair<std::size_t, unsigned> key{pos, mask};
        if (failed_.count(key) != 0) return false;

        unsigned bit = kBold;
        Closer closer = Closer::Bold;
        if (style == Style::Italic) { bit = kItalic; closer = Closer::Italic; }
        if (style == Style::Link) { bit = kLink; closer = Closer::LinkText; }

        const std::size_t out_mark = out_.size();
        const std::size_t units_mark = units_;
        const std::size_t range_mark = ranges_.size();
        const std::size_t offset = units_;

        std::size_t p = pos + marker_len;
        std::string url;
        if (seq(p, mask | bit, closer, p, url)) {
            ranges_.push_back(Pending{style, offset, units_ - offset, url, pos});
            new_pos = p;
            return true;
        }
        out_.resize(out_mark);
        units_ = units_mark;
        ranges_.resize(range_mark);
        failed_.insert(key);
        return false;
    }

    // Rule 7: the content of `code` is copied verbatim.
    bool open_code(std::size_t pos, unsigned mask, std::size_t& new_pos) {
        const std::size_t start = pos + 1;
        const std::size_t q = next_code_closer_[start + 1];  // closer must be > start
        if (q >= n_) return false;
        if ((mask & kLink) != 0 && bracket_prefix_[q] - bracket_prefix_[start] > 0) {
            return false;  // would put ']' into link text
        }
        const std::size_t offset = units_;
        for (std::size_t i = start; i < q; ++i) emit(s_[i]);
        ranges_.push_back(Pending{Style::Code, offset, units_ - offset, std::string(), pos});
        new_pos = q + 1;
        return true;
    }

    const std::u32string& s_;
    const std::size_t n_;
    std::u32string out_;
    std::size_t units_ = 0;
    std::vector<Pending> ranges_;
    std::set<std::pair<std::size_t, unsigned>> failed_;
    std::vector<std::size_t> next_code_closer_;
    std::vector<std::size_t> bracket_prefix_;
};

void json_string(std::string& out, const std::string& s) {
    static const char* hex = "0123456789abcdef";
    out.push_back('"');
    for (char ch : s) {
        const unsigned char c = static_cast<unsigned char>(ch);
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (c < 0x20) {
                    out += "\\u00";
                    out.push_back(hex[c >> 4]);
                    out.push_back(hex[c & 0xF]);
                } else {
                    out.push_back(ch);
                }
        }
    }
    out.push_back('"');
}

}  // namespace

const char* style_name(Style style) {
    switch (style) {
        case Style::Bold: return "bold";
        case Style::Italic: return "italic";
        case Style::Code: return "code";
        case Style::Link: return "link";
    }
    return "";
}

bool is_valid_utf8(const std::string& input) {
    std::u32string tmp;
    return decode_utf8(input, tmp);
}

std::optional<ParseResult> parse(const std::string& input) {
    std::u32string src;
    if (!decode_utf8(input, src)) return std::nullopt;
    Parser parser(src);
    return parser.run();
}

std::optional<std::string> escape(const std::string& input) {
    std::u32string src;
    if (!decode_utf8(input, src)) return std::nullopt;
    std::string out;
    out.reserve(input.size() + input.size() / 8);
    for (char32_t c : src) {
        if (is_service(c)) out.push_back('\\');
        append_utf8(out, c);
    }
    return out;
}

std::string to_json(const ParseResult& result) {
    std::string out = "{\"text\": ";
    json_string(out, result.text);
    out += ", \"ranges\": [";
    bool first = true;
    for (const Range& r : result.ranges) {
        if (!first) out += ", ";
        first = false;
        out += "{\"style\": \"";
        out += style_name(r.style);
        out += "\", \"offset\": " + std::to_string(r.offset);
        out += ", \"length\": " + std::to_string(r.length);
        out += ", \"url\": ";
        json_string(out, r.url);
        out += "}";
    }
    out += "]}";
    return out;
}

}  // namespace msgfmt
