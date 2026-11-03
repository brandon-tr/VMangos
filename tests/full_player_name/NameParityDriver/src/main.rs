use benilla_ui::script::{
    check_full_char_name, prepare_full_char_name, NameCharsets, NameLists,
};
use std::io::{self, BufRead, Write};

struct Lists {
    mask: u32,
}

impl NameLists for Lists {
    fn names_profanity(&self, name: &str) -> bool {
        (self.mask & 1) != 0 && lower(name).contains("badname")
    }
    fn chat_profanity(&self, name: &str) -> bool {
        (self.mask & 2) != 0 && lower(name).contains("badchat")
    }
    fn names_reserved(&self, name: &str) -> bool {
        (self.mask & 4) != 0 && lower(name).contains("badres")
    }
}

fn lower(value: &str) -> String {
    value
        .chars()
        .map(|c| {
            if c.is_ascii_uppercase() {
                c.to_ascii_lowercase()
            } else {
                c
            }
        })
        .collect()
}

fn hex_encode(value: &str) -> String {
    let mut out = String::new();
    for byte in value.as_bytes() {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

fn hex_decode(hex: &str) -> Option<String> {
    if hex.len() % 2 != 0 {
        return None;
    }
    let mut bytes = Vec::new();
    for pair in hex.as_bytes().chunks(2) {
        let text = std::str::from_utf8(pair).ok()?;
        bytes.push(u8::from_str_radix(text, 16).ok()?);
    }
    String::from_utf8(bytes).ok()
}

fn pair_fields(cs: NameCharsets) -> (u8, u8) {
    let debug = format!("{cs:?}");
    let primary = debug
        .split("primary:")
        .nth(1)
        .and_then(|tail| tail.split(',').next())
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(0);
    let secondary = debug
        .split("secondary:")
        .nth(1)
        .and_then(|tail| tail.split('}').next())
        .and_then(|value| value.trim().parse().ok())
        .unwrap_or(0);
    (primary, secondary)
}

fn main() {
    let stdin = io::stdin();
    let mut out = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = line.expect("stdin");
        if line.is_empty() {
            continue;
        }
        if line.starts_with('P') {
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() != 3 {
                std::process::exit(2);
            }
            let region: i32 = parts[1].parse().unwrap();
            let locale: usize = parts[2].parse().unwrap();
            let cs = NameCharsets::for_client(region, locale);
            let (primary, secondary) = pair_fields(cs);
            writeln!(out, "P\t{region}\t{locale}\t{primary}\t{secondary}").unwrap();
            continue;
        }
        if !line.starts_with('N') {
            std::process::exit(3);
        }
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() != 6 {
            std::process::exit(4);
        }
        let id = parts[1];
        let region: i32 = parts[2].parse().unwrap();
        let locale: usize = parts[3].parse().unwrap();
        let mask: u32 = parts[4].parse().unwrap();
        let name = hex_decode(parts[5]).expect("utf8 corpus");
        let cs = NameCharsets::for_client(region, locale);
        let lists = Lists { mask };
        let raw = check_full_char_name(Some(&name), &lists, cs);
        let prepared = prepare_full_char_name(Some(&name), &lists, cs);
        let (status, canonical) = match prepared {
            Ok(value) => (0x52u8, value),
            Err(status) => (status, String::new()),
        };
        let (primary, secondary) = pair_fields(cs);
        let unchanged = if status != 0x52 || canonical == name { 1 } else { 0 };
        let canonical = if status == 0x52 {
            hex_encode(&canonical)
        } else {
            "-".to_string()
        };
        writeln!(
            out,
            "N\t{id}\t{raw}\t{status}\t{unchanged}\t{primary}\t{secondary}\t{canonical}"
        )
        .unwrap();
    }
}
