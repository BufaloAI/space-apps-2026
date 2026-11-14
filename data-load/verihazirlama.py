import glob
import os
import pandas as pd
import xarray as xr

def prepare_clean_daily_data(input_dir="/home/nasko/Projeler/smap-verileri", output_file="field_shift_optimized_features.csv"):
    """
    NASA Earthdata ham dosyalarını işler:
    - Konum bilgisi (lat/lon) olmayanları kesinlikle eler (Asla uydurma veri üretmez).
    - Gün içi saatlik/dakikalık tekrarları atar, aynı bölgenin günler arasındaki 
      değişimini izleyebilmek için günde max 1 tekil kayda indirger.
    - ML ekibinin doğrudan model eğitebileceği optimize edilmiş bir CSV üretir.
    """
    file_pattern = os.path.join(input_dir, "*")
    files = [f for f in glob.glob(file_pattern) if f.endswith(('.h5', '.nc4', '.nc', '.he5'))]
    
    if not files:
        print("Uyarı: İşlenecek veri dosyası bulunamadı. Önce verilerin indiğinden emin olun.")
        return None
    
    print(f"Toplam {len(files)} adet ham dosya işleme alındı. Filtreleniyor...")
    processed_chunks = []
    
    for file_path in files:
        print(f"İşleniyor: {os.path.basename(file_path)}")
        try:
            with xr.open_dataset(file_path) as ds:
                df = ds.to_dataframe().reset_index()
                
                # 1. KURAL: Enlem ve boylam (lat/lon) zorunludur. Yoksa atla.
                if 'lat' not in df.columns or 'lon' not in df.columns:
                    print(f"-> Uyarı: Bu dosyada lat/lon sütunu yok, atlanıyor.")
                    continue
                
                # Boş koordinatları temizle
                df = df.dropna(subset=['lat', 'lon'])
                if df.empty:
                    continue
                
                # 2. KURAL: Gün içi saatlik/dakikalık şişmeleri önle, günlük tekilleştir
                if 'time' in df.columns:
                    df['date'] = pd.to_datetime(df['time']).dt.date
                    # Aynı gün, aynı enlem ve boylamdaki ilk kaydı alarak gün içindeki gereksiz kalabalığı seyrelt
                    df = df.groupby(['date', 'lat', 'lon']).first().reset_index()
                else:
                    # Zaman sütunu yoksa bile konum bazlı tekilleştir
                    df = df.drop_duplicates(subset=['lat', 'lon'])
                
                processed_chunks.append(df)
                
        except Exception as e:
            print(f"Dosya okuma hatası ({file_path}): {e}")
            
    if processed_chunks:
        # Tüm parçaları tek bir master tabloda birleştir
        master_df = pd.concat(processed_chunks, ignore_index=True)
        print(f"\nFiltreleme Tamamlandı! Toplam Temiz Satır Sayısı: {master_df.shape[0]}")
        
        # ML ekibi için CSV olarak kaydet
        master_df.to_csv(output_file, index=False)
        print(f"[BAŞARILI] Optimize edilmiş dosya oluşturuldu: {output_file}")
        return master_df
    else:
        print("Kriterlere uygun geçerli veri bulunamadı.")
        return None

if __name__ == "__main__":
    prepare_clean_daily_data()