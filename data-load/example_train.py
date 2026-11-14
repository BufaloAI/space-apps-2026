from nasa_dataloader import get_nasa_data_loaders

# Kullanacağın özellik sütunlarını belirle (örneğin: koordinatlar ve zaman bileşenleri)
feature_columns = ['lat', 'lon', 'yil', 'ay', 'gun', 'saat']

# DataLoader'ları oluştur
train_loader, test_loader = get_nasa_data_loaders(
    csv_path="/data/nasa_pipeline/field_shift_optimized_features.csv",
    feature_cols=feature_columns,
    batch_size=64
)

# Chapter 6 rehberindeki gibi batch'ler halinde iterasyon testi:
for idx, (x, y) in enumerate(train_loader):
    print(f"Batch {idx+1} Özellik Boyutu:", x.shape, "Etiket Boyutu:", y.shape)
    if idx == 2:  # İlk 3 batch'i görmek yeterli
        break