import os
import earthaccess

def nasa_verilerini_indir():
    # Earthdata kimlik doğrulamanı yapar (ilk çalıştırmada kullanıcı adı/şifre sorabilir)
    print("NASA Earthdata oturumu açılıyor...")
    auth = earthaccess.login(persist=True)

    if not auth.authenticated:
        print("Kimlik doğrulama başarısız! Lütfen NASA Earthdata hesabınızı kontrol edin.")
        return

    # Hedef klasörü oluştur
    output_dir = "../smap_verileri"
    os.makedirs(output_dir, exist_ok=True)

    print("NASA SMAP L4 verileri aranıyor...")
    
    # Space Apps projen için örnek arama (SMAP L4 Global Soil Moisture)
    # Kıta/bölge kutusu (Bounding Box) ve tarih aralığını kendi çalışma alanına göre güncelleyebilirsin.
    # Örnek: [Min_Boylam, Min_Enlem, Max_Boylam, Max_Enlem] (Örn: Türkiye çevresi veya tarım arazisi)
    results = earthaccess.search_data(
        short_name='SPL4SMGP',  # SMAP L4 Soil Moisture Product
        temporal=("2023-05-01", "2023-09-30"),  
        bounding_box=(26.0, 36.0, 45.0, 42.0)    
    )

    print(f"Toplam {len(results)} adet uydu dosyası bulundu. İndiriliyor...")


    earthaccess.download(results, output_dir)
    print(f"[BAŞARILI] Tüm ham veriler '{output_dir}' klasörüne indirildi!")

if __name__ == "__main__":
    nasa_verilerini_indir()
    