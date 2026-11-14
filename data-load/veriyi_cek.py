import os
import earthaccess

def tum_dunya_nasa_verilerini_indir():
    print("NASA Earthdata oturumu başlatılıyor...")
    auth = earthaccess.login(persist=True)

    if not auth.authenticated:
        print("Kimlik doğrulama başarısız! Lütfen NASA Earthdata hesabınızı kontrol edin.")
        return

    # Küresel veriler çok büyük olacağı için klasör adını güncelledik
    output_dir = "./global_nasa_verileri"
    os.makedirs(output_dir, exist_ok=True)

    # Hackathon için kritik tarım, iklim ve toprak veri setleri
    veri_setleri = {
        "Toprak_Nemi_SMAP": "SPL4SMGP",       # SMAP L4 Toprak Nemi
        "Bitki_Ortusu_NDVI": "MOD13Q1",  # MODIS Bitki Sağlığı
        "Yagis_Miktari_GPM": "GPM_3IMERGDF",  # GPM Yağış Verisi
        "Sicaklik_MODIS": "MOD11A1"        # Kara Yüzeyi Sıcaklığı
    }
    
    # 2024-2025 yılları arası tüm zaman adımları
    tarih_araligi = ("2024-01-01", "2025-12-31")

    for veri_adi, short_name in veri_setleri.items():
        print(f"\n[{veri_adi}] ({short_name}) için TÜM DÜNYA verileri aranıyor...")
        try:
            # bounding_box parametresi kaldırıldı -> Artık KÜRESEL (Global) arama yapıyor!
            results = earthaccess.search_data(
                short_name=short_name,
                temporal=tarih_araligi
            )
            
            if results:
                print(f"-> {len(results)} adet küresel dosya bulundu. İndiriliyor...")
                earthaccess.download(results, output_dir)
                print(f"-> [BAŞARILI] {veri_adi} küresel verileri indirildi.")
            else:
                print(f"-> Uyarı: Bu kriterlere uygun veri bulunamadı.")
        except Exception as e:
            print(f"-> Hata oluştu ({veri_adi}): {e}")

    print(f"\n[İŞLEM TAMAMLANDI] Tüm dünya verileri '{output_dir}' klasörüne aktarıldı!")

if __name__ == "__main__":
    tum_dunya_nasa_verilerini_indir()