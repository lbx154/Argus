const REDACTED: &str = "<redacted>";

/// Redact the credential forms the desktop itself can persist or relay.
/// This deliberately runs before data reaches the file logger or diagnostics.
pub fn redact_sensitive_text(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    let mut rest = text;
    loop {
        let lowered = rest.to_ascii_lowercase();
        let candidates = [
            lowered.find("\"token\""),
            lowered.find("?token="),
            lowered.find("&token="),
            lowered.find("authorization: bearer "),
        ];
        let Some(offset) = candidates.into_iter().flatten().min() else {
            result.push_str(rest);
            break;
        };
        result.push_str(&rest[..offset]);
        let tail = &rest[offset..];
        let tail_lower = &lowered[offset..];

        if tail_lower.starts_with("\"token\"") {
            let Some(colon) = tail.find(':') else {
                result.push_str(tail);
                break;
            };
            let before_value = &tail[..=colon];
            let after_colon = &tail[colon + 1..];
            let whitespace_len = after_colon.len() - after_colon.trim_start().len();
            let whitespace = &after_colon[..whitespace_len];
            let value = &after_colon[whitespace_len..];
            if let Some(after_open) = value.strip_prefix('"') {
                if let Some(end) = after_open.find('"') {
                    result.push_str(before_value);
                    result.push_str(whitespace);
                    result.push('"');
                    result.push_str(REDACTED);
                    result.push('"');
                    rest = &after_open[end + 1..];
                    continue;
                }
            }
            result.push_str(tail);
            break;
        }

        if tail_lower.starts_with("?token=") || tail_lower.starts_with("&token=") {
            let prefix_len = 7;
            result.push_str(&tail[..prefix_len]);
            result.push_str(REDACTED);
            let value = &tail[prefix_len..];
            let end = value
                .char_indices()
                .find_map(|(index, character)| {
                    matches!(
                        character,
                        '&' | ' ' | '\t' | '\r' | '\n' | '\"' | '\'' | '<' | '>'
                    )
                    .then_some(index)
                })
                .unwrap_or(value.len());
            rest = &value[end..];
            continue;
        }

        const BEARER: &str = "authorization: bearer ";
        result.push_str(&tail[..BEARER.len()]);
        result.push_str(REDACTED);
        let value = &tail[BEARER.len()..];
        let end = value
            .char_indices()
            .find_map(|(index, character)| {
                matches!(
                    character,
                    ' ' | '\t' | '\r' | '\n' | ',' | '\"' | '\'' | '}'
                )
                .then_some(index)
            })
            .unwrap_or(value.len());
        rest = &value[end..];
    }
    result
}

#[cfg(test)]
mod tests {
    use super::redact_sensitive_text;

    #[test]
    fn redacts_json_urls_and_bearer_headers() {
        let raw = concat!(
            "{\"token\": \"json-secret\"}\n",
            "GET /?token=url-secret HTTP/1.1\n",
            "WebSocket /stream?replay=40&token=ws-secret [accepted]\n",
            "Authorization: Bearer bearer-secret"
        );
        let redacted = redact_sensitive_text(raw);
        for secret in ["json-secret", "url-secret", "ws-secret", "bearer-secret"] {
            assert!(!redacted.contains(secret));
        }
        assert!(redacted.contains("\"token\": \"<redacted>\""));
        assert!(redacted.contains("?token=<redacted>"));
        assert!(redacted.contains("&token=<redacted>"));
    }
}
