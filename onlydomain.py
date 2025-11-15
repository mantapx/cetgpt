import re

def extract_domains(text):
    # Regex untuk menangkap domain
    pattern = r'\b([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
    domains = re.findall(pattern, text)
    return domains

def main():
    print("== Extract Domain Only ==")
    input_file = input("Masukkan nama file input: ").strip()
    output_file = input("Masukkan nama file output: ").strip()

    try:
        with open(input_file, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()

        domains = extract_domains(data)

        # Buat unik + urut
        domains = sorted(set(domains))

        with open(output_file, "w", encoding="utf-8") as f:
            for d in domains:
                f.write(d + "\n")

        print(f"Berhasil! Total domain: {len(domains)}")
        print(f"Hasil disimpan ke: {output_file}")

    except Exception as e:
        print("Terjadi error:", e)

if __name__ == "__main__":
    main()
