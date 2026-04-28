import json
import os
import re

def load_zakat_data(filename="zakat_data.json"):
    if not os.path.exists(filename):
        print(f"Error: File {filename} tidak ditemukan.")
        return None
    with open(filename, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_float_input(prompt):
    while True:
        val = input(prompt).strip()
        if not val:
            return 0.0
        try:
            return float(val.replace(',', ''))
        except ValueError:
            print("  -> Masukkan angka yang valid.")

def get_int_input(prompt):
    while True:
        val = input(prompt).strip()
        if not val:
            return 0
        try:
            return int(val.replace(',', ''))
        except ValueError:
            print("  -> Masukkan bilangan/nomor bulat yang valid.")

def process_field_input(field):
    """
    Fungsi pintar untuk mengesan sama ada label meminta RM, Bilangan, atau nilai yang telah dibaca dari web.
    """
    label = field['label']
    lbl_lower = label.lower()
    
    # Ambil nilai default jika wujud (contohnya untuk Ketua Keluarga)
    # Buang koma jika ada, contoh: "14,580" -> "14580"
    default_val_str = field.get('input_value', '').replace(',', '').strip()
    
    # 1. Kes Khas: Ketua Keluarga (Baca terus nilai yang diekstrak oleh scraper)
    if "ketua keluarga" in lbl_lower:
        if default_val_str:
            try:
                kadar = float(default_val_str)
                print(f"      • {label}: RM {kadar:,.2f} (Dibaca dari sistem)")
                return kadar
            except ValueError:
                pass # Teruskan ke bawah jika gagal ditukar ke nombor
        
        # Jika nilai gagal dibaca oleh scraper atas sebab tertentu, minta pengguna masukkan:
        return get_float_input(f"      • RM bagi {label}: RM ")

    # 2. Semak corak: "RM[angka] / Seorang" (contoh: "RM4,944 / Seorang")
    match = re.search(r'RM\s*([\d,.]+)\s*/\s*Seorang', label, re.IGNORECASE)
    
    if match:
        rate_str = match.group(1).replace(',', '')
        rate = float(rate_str)
        
        bilangan = get_int_input(f"      • Bilangan untuk {label}: ")
        total_rm = bilangan * rate
        
        if bilangan > 0:
            print(f"        -> Dikira: {bilangan} x RM {rate:,.2f} = RM {total_rm:,.2f}")
            
        return total_rm

    # 3. Default: Minta nilai wang (RM)
    return get_float_input(f"      • RM bagi {label}: RM ")

def main():
    data = load_zakat_data()
    if not data:
        return

    print("="*65)
    print(" KALKULATOR ZAKAT (DINAMIK BERDASARKAN SCRAPER)")
    print("="*65)

    print("\nPilih Jenis Zakat:")
    print("1. Zakat Pendapatan")
    print("2. Zakat Perniagaan")
    
    pilihan_utama = input("Masukkan pilihan (1/2): ").strip()

    zakat_key = ""
    
    if pilihan_utama == '1':
        print("\nPilih Kategori Zakat Pendapatan:")
        print("1. Tanpa Tolakan")
        print("2. Dengan Tolakan")
        pilihan_sub = input("Masukkan pilihan (1/2): ").strip()
        
        if pilihan_sub == '1':
            zakat_key = "zakat_pendapatan_tanpa_tolakan"
        elif pilihan_sub == '2':
            zakat_key = "zakat_pendapatan_dengan_tolakan"
        else:
            print("Pilihan tidak valid.")
            return
    elif pilihan_utama == '2':
        zakat_key = "zakat_perniagaan"
    else:
        print("Pilihan tidak valid.")
        return

    if zakat_key not in data or not data[zakat_key]:
        print(f"Data '{zakat_key}' kosong. Sila periksa JSON anda.")
        return

    # Akumulator Bahagian
    totals = {'A': 0.0, 'B': 0.0, 'C': 0.0, 'E': 0.0}

    print("\n" + "─"*65)
    print(" SILA MASUKKAN MAKLUMAT BERIKUT (Tekan Enter jika tiada / 0)")
    print("─"*65)

    sections = data[zakat_key]
    for heading, fields in sections.items():
        print(f"\n  ▌ {heading}")
        
        # Tentukan bahagian mana yang sedang diproses (A, B, C, atau E)
        current_section = 'A' # Default
        if 'bahagian b' in heading.lower(): current_section = 'B'
        elif 'bahagian c' in heading.lower(): current_section = 'C'
        elif 'bahagian e' in heading.lower(): current_section = 'E'

        for field in fields:
            label = field['label']
            lbl_lower = label.lower().strip()
            
            # Buat tapisan yang lebih spesifik supaya tidak terlangkau "Jumlah Tunai..."
            is_total_row = (
                lbl_lower.startswith("jumlah a") or 
                lbl_lower.startswith("jumlah b") or 
                lbl_lower.startswith("jumlah c") or 
                lbl_lower.startswith("jumlah e")
            )
            
            if is_total_row or lbl_lower == "rm":
                continue
            
            nilai = process_field_input(field)
            totals[current_section] += nilai

    # --- PENGIRAAN FORMULA ZAKAT ---
    print("\n" + "="*65)
    print(" RINGKASAN & HASIL PENGIRAAN")
    print("="*65)
    
    print(f"  Jumlah A (Pendapatan/Aset)     : RM {totals['A']:,.2f}")
    if totals['B'] > 0: print(f"  Jumlah B (Liabiliti/Kifayah)   : RM {totals['B']:,.2f}")
    if totals['C'] > 0: print(f"  Jumlah C (Tolakan Lain)        : RM {totals['C']:,.2f}")
    if totals['E'] > 0: print(f"  Jumlah E (Tabung Haji)         : RM {totals['E']:,.2f}")

    total_zakat = 0.0

    if zakat_key == "zakat_pendapatan_tanpa_tolakan":
        total_zakat = totals['A'] * 0.025
        print("\n  [Formula]: Jumlah A * 2.5%")
        
    elif zakat_key == "zakat_pendapatan_dengan_tolakan":
        # Jumlah A - (B + C + E)
        pendapatan_bersih = max(0, totals['A'] - (totals['B'] + totals['C'] + totals['E']))
        total_zakat = pendapatan_bersih * 0.025
        print(f"  Pendapatan Bersih (Layak Zakat): RM {pendapatan_bersih:,.2f}")
        print("\n  [Formula]: [Jumlah A - (Jumlah B + Jumlah C + Jumlah E)] * 2.5%")
        
    elif zakat_key == "zakat_perniagaan":
        # Jumlah A - B
        harta_bersih = max(0, totals['A'] - totals['B'])
        total_zakat = harta_bersih * 0.025
        print(f"  Harta Bersih (Layak Zakat)     : RM {harta_bersih:,.2f}")
        print("\n  [Formula]: [Jumlah A - Jumlah B] * 2.5%")

    print("─"*65)
    print(f"  JUMLAH ZAKAT YANG PERLU DIBAYAR: RM {total_zakat:,.2f}")
    print("="*65)

if __name__ == "__main__":
    main()