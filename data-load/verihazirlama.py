import glob
import os
import pandas as pd
import xarray as xr

def veri_temizle_ve_birlestir(input_dir="./global_nasa_verileri", output_file="field_shift_optimized_features.csv"):
    """
    NASA ham verilerini işler:
    - Koordinatları (lat/lon) filtreler.
    - Zaman damgasını hem istenen string formatına (YYYY.MM.DD.HH) 
      hem de XGBoost'un doğrudan kullanabileceği sayısal sütunlara (yil, ay, gun, saat) ayırır.
    """
    file_pattern = os.path.join(input_dir, "*")
    files = [f for f in glob.glob(file_pattern) if f.endswith(('.h5', '.nc4', '.nc', '.he5'))]
    
    if not files:
        print("İşlenecek veri dosyası bulunamadı. Önce verileri indirdiğinizden emin olun.")
        return None
    
    print(f"Toplam {len(files)} adet dosya işleniyor...")
    processed_chunks = []
    
    for file_path in files:
        print(f"İşleniyor: {os.path.basename(file_path)}")
        try:
            with xr.open_dataset(file_path) as ds:
                df = ds.to_dataframe().reset_index()
                
                # 1. Enlem ve boylam zorunluluğu
                if 'lat' not in df.columns or 'lon' not in df.columns:
                    continue
                
                df = df.dropna(subset=['lat', 'lon'])
                if df.empty:
                    continue
                
                # 2. Zaman sütununu bulma ve biçimlendirme
                time_col = None
                for col in ['time', 'valid_time', 'date']:
                    if col in df.columns:
                        time_col = col
                        break
                
                if time_col:
                    # Datetime formatına çevir
                    df[time_col] = pd.to_datetime(df[time_col], errors='coerce')
                    df = df.dropna(subset=[time_col])
                    
                    # İstediğin format: Yıl.Ay.Gün.Saat (Örn: 2026.11.14.18)
                    df['zaman_formatli'] = df[time_col].dt.strftime('%Y.%m.%d.%H')
                    
                    # XGBoost için modelin doğrudan anlayacağı sayısal sütunlar:
                    df['yil'] = df[time_col].dt.year
                    df['ay'] = df[time_col].dt.month
                    df['gun'] = df[time_col].dt.day
                    df['saat'] = df[time_col].dt.hour
                
                processed_chunks.append(df)
                
        except Exception as e:
            print(f"Hata oluştu ({file_path}): {e}")
            
    if processed_chunks:
        master_df = pd.concat(processed_chunks, ignore_index=True)
        master_df.to_csv(output_file, index=False)
        print(f"\n[BAŞARILI] Optimize edilen veriler kaydedildi: {output_file}")
        print(f"Toplam Satır Sayısı: {master_df.shape[0]}")
        return master_df
    else:
        print("Uygun veri bulunamadı.")
        return None

if __name__ == "__main__":
    veri_temizle_ve_birlestir()