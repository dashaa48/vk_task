// msgfmt: converts a Markdown subset into plain text + style ranges
// (offsets/lengths are measured in UTF-16 code units).
#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace msgfmt {

enum class Style { Bold, Italic, Code, Link };

// "bold", "italic", "code", "link".
const char* style_name(Style style);

struct Range {
    Style style;
    std::size_t offset;  // UTF-16 code units in the plain text
    std::size_t length;  // UTF-16 code units
    std::string url;     // empty unless style == Link
};

struct ParseResult {
    std::string text;  // plain text, UTF-8
    std::vector<Range> ranges;
};

// True if `input` is well-formed UTF-8 (no overlongs, surrogates, > U+10FFFF).
bool is_valid_utf8(const std::string& input);

// Parses the markup. Returns std::nullopt for invalid UTF-8.
std::optional<ParseResult> parse(const std::string& input);

// Prefixes every service character (* _ ` [ ] ( ) \) with a backslash.
// Returns std::nullopt for invalid UTF-8.
std::optional<std::string> escape(const std::string& input);

// {"text": ..., "ranges": [...]} (single line, UTF-8, no trailing newline).
std::string to_json(const ParseResult& result);

}  // namespace msgfmt
