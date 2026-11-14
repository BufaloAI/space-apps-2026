import torch
from torch.utils.data import Dataset, DataLoader
import pandas as pd
import numpy as np

class NASAFieldDataset(Dataset):
    """
    Sebastian Raschka's PyTorch 1-Hour (Chapter 6) style Custom Dataset 
    adapted for NASA Field Shift multi-product tabular features (SMAP, MODIS, GPM).
    """
    def __init__(self, dataframe, feature_columns, target_column=None):
        self.features = torch.tensor(dataframe[feature_columns].values, dtype=torch.float32)
        if target_column and target_column in dataframe.columns:
            self.labels = torch.tensor(dataframe[target_column].values, dtype=torch.float32)
        else:
            self.labels = torch.zeros(len(dataframe), dtype=torch.float32)

    def __getitem__(self, index):
        one_x = self.features[index]
        one_y = self.labels[index]
        return one_x, one_y

    def __len__(self):
        return len(self.labels)


def get_nasa_data_loaders(csv_path, feature_cols, batch_size=32, test_size=0.2):
    """
    Initializes custom Dataset and creates DataLoaders with shuffle and drop_last 
    following Sebastian Raschka's best practices.
    """
    df = pd.read_csv(csv_path)
    df = df.dropna(subset=feature_cols)
    
    train_df = df.sample(frac=(1 - test_size), random_state=123)
    test_df = df.drop(train_df.index)
    
    train_ds = NASAFieldDataset(train_df, feature_cols)
    test_ds = NASAFieldDataset(test_df, feature_cols)
    
    torch.manual_seed(123)
    train_loader = DataLoader(
        dataset=train_ds,
        batch_size=batch_size,
        shuffle=True,
        num_workers=0,
        drop_last=True
    )
    
    test_loader =DataLoader(
        dataset=test_ds,
        batch_size=batch_size,
        shuffle=False,
        num_workers=0
    )
    
    return train_loader, test_loader