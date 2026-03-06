import os
import glob

def gabungkan_weblist():
    # Konfigurasi nama folder dan file output
    folder_sumber = 'domain'
    file_hasil = 'persatuan.txt'

    # Cek apakah folder 'domain' ada
    if not os.path.exists(folder_sumber):
        print(f"[-] Folder '{folder_sumber}' tidak ditemukan!")
        print(f"[-] Buat folder bernama '{folder_sumber}' dan masukkan file .txt ke dalamnya.")
        return

    # Mencari semua file .txt di dalam folder 'domain'
    path_pencarian = os.path.join(folder_sumber, '*.txt')
    daftar_file = glob.glob(path_pencarian)

    if not daftar_file:
        print(f"[-] Tidak ada file .txt di dalam folder '{folder_sumber}'.")
        return

    print(f"[+] Ditemukan {len(daftar_file)} file .txt. Memulai proses...")

    # Menggunakan 'set' untuk otomatis membuang domain yang duplikat
    domain_unik = set()
    total_baris_dibaca = 0

    # Membaca setiap file .txt yang ditemukan
    for file_path in daftar_file:
        print(f"    -> Membaca: {os.path.basename(file_path)}")
        try:
            # Menggunakan encoding utf-8 untuk menghindari error karakter aneh
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    total_baris_dibaca += 1
                    # Membersihkan spasi atau enter di awal/akhir teks
                    domain = line.strip()
                    
                    # Jika baris tidak kosong, masukkan ke dalam set
                    if domain:
                        domain_unik.add(domain)
        except Exception as e:
            print(f"[-] Error membaca {file_path}: {e}")

    # Menyimpan hasil gabungan ke persatuan.txt
    print(f"\n[+] Menyimpan hasil ke '{file_hasil}'...")
    try:
        with open(file_hasil, 'w', encoding='utf-8') as f_out:
            for domain in domain_unik:
                f_out.write(domain + '\n')
        
        print("\n=== PROSES SELESAI ===")
        print(f"Total baris mentah dibaca  : {total_baris_dibaca}")
        print(f"Total domain unik disimpan : {len(domain_unik)}")
        
    except Exception as e:
        print(f"[-] Error saat menyimpan file: {e}")

if __name__ == "__main__":
    gabungkan_weblist()
