import os
import glob
import pandas as pd
import xarray as xr
import earthaccess

class NASADataPipeline:
    def __init__(self, base_dir: str = "/data/nasa_pipeline", temporal_range: tuple = ("2024-01-01", "2025-12-31")):
        """
        NASA uydu verilerini indirmek ve işlemek için OOP tabanlı pipeline sınıfı.
        """
        self.base_dir = base_dir
        self.raw_data_dir = os.path.join(base_dir, "raw_data")
        self.processed_file_path = os.path.join(base_dir, "field_shift_optimized_features.csv")
        self.temporal_range = temporal_range
        
        # Hedef Linux dizinlerinin varlığını kontrol et ve oluştur
        os.makedirs(self.raw_data_dir, exist_ok=True)
        
        # Hackathon için kritik veri setleri
        self.datasets = {
            "Toprak_Nemi_SMAP": "SPL4SMGP",
            "Bitki_Ortusu_NDVI": "MOD13Q1",
            "Yagis_Miktari_GPM": "GPM_3IMERGDF",
            "Sicaklik_MODIS": "MOD11A1"
        }

    def authenticate(self):
        """NASA Earthdata kimlik doğrulamasını gerçekleştirir."""
        print("NASA Earthdata oturumu başlatılıyor...")
        auth = earthaccess.login(persist=True)
        if not auth.authenticated:
            raise ConnectionError("Kimlik doğrulama başarısız! Lütfen NASA Earthdata hesabınızı kontrol edin.")
        print("[BAŞARILI] Oturum açıldı.")

    def download_data(self):
        """Belirtilen tarih aralığında tüm dünya verilerini indirir."""
        self.authenticate()
        
        for name, short_name in self.datasets.items():
            print(f"\n[{name}] ({short_name}) için TÜM DÜNYA verileri aranıyor...")
            try:
                results = earthaccess.search_data(
                    short_name=short_name,
                    temporal=self.temporal_range
                )
                
                if results:
                    print(f"-> {len(results)} adet küresel dosya bulundu. İndiriliyor...")
                    earthaccess.download(results, self.raw_data_dir)
                    print(f"-> [BAŞARILI] {name} verileri indirildi.")
                else:
                    print(f"-> Uyarı: Bu kriterlere uygun veri bulunamadı.")
            except Exception as e:
                print(f"-> Hata oluştu ({name}): {e}")

    def process_data(self):
        """
        İndirilen ham verileri temizler, koordinat filtrelemesi yapar ve 
        XGBoost'un doğrudan anlayacağı sayısal zaman sütunlarına (yil, ay, gun, saat) ayırır.
        """
        file_pattern = os.path.join(self.raw_data_dir, "*")
        files = [f for f in glob.glob(file_pattern) if f.endswith(('.h5', '.nc4', '.nc', '.he5'))]
        
        if not files:
            print(f"İşlenecek veri dosyası bulunamadı: {self.raw_data_dir}")
            return None
        
        print(f"\nToplam {len(files)} adet dosya işleniyor...")
        processed_chunks = []
        
        for file_path in files:
            print(f"İşleniyor: {os.path.basename(file_path)}")
            try:
                with xr.open_dataset(file_path) as ds:
                    df = ds.to_dataframe().reset_index()
                    
                    # 1. Koordinat kontrolü
                    if 'lat' not in df.columns or 'lon' not in df.columns:
                        continue
                    
                    df = df.dropna(subset=['lat', 'lon'])
                    if df.empty:
                        continue
                    
                    # 2. Zaman sütununu bulma ve biçimlendirme
                    time_col = next((col for col in ['time', 'valid_time', 'date'] if col in df.columns), None)
                    
                    if time_col:
                        df[time_col] = pd.to_datetime(df[time_col], errors='coerce')
                        df = df.dropna(subset=[time_col])
                        
                        # İnsan okuması için format: Yıl.Ay.Gün.Saat (Örn: 2026.11.14.18)
                        df['zaman_formatli'] = df[time_col].dt.strftime('%Y.%m.%d.%H')
                        
                        # XGBoost için modelin doğrudan anlayacağı sayısal sütunlar
                        df['yil'] = df[time_col].dt.year
                        df['ay'] = df[time_col].dt.month
                        df['gun'] = df[time_col].dt.day
                        df['saat'] = df[time_col].dt.hour
                    
                    processed_chunks.append(df)
                    
            except Exception as e:
                print(f"Dosya işlenirken hata oluştu ({file_path}): {e}")
                
        if processed_chunks:
            master_df = pd.concat(processed_chunks, ignore_index=True)
            master_df.to_csv(self.processed_file_path, index=False)
            print(f"\n[BAŞARILI] Optimize edilen veriler kaydedildi: {self.processed_file_path}")
            print(f"Toplam Satır Sayısı: {master_df.shape[0]}")
            return master_df
        else:
            print("İşlenebilecek uygun veri bulunamadı.")
            return None

if __name__ == "__main__":
    # Pipeline nesnesini Linux /data dizin yapısıyla başlatıyoruz
    pipeline = NASADataPipeline(base_dir="/data/nasa_pipeline")
    
    # 1. Adım: Verileri İndir
    # pipeline.download_data()
    
    # 2. Adım: Verileri İşle ve XGBoost formatına getir
    pipeline.process_data()