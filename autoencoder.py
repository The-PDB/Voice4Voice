import math

import torch
from torch import nn, Tensor
import torch.nn.functional as F
from einops.layers.torch import Rearrange
from einops.einops import rearrange
import pytorch_lightning as pl
from utils import audio_to_spectrogram, spectrogram_to_image, spectrogram_to_audio


class Encoder(pl.LightningModule):
    def __init__(self, kernel_size, encoded_dim):
        super().__init__()

        self.encoder = nn.Sequential(
            Rearrange("(b c) w h -> b c w h", c=1),
            nn.Conv2d(1, 32, kernel_size=kernel_size, stride=2, padding=1),
            # nn.BatchNorm2d(32),
            # nn.LeakyReLU(),
            nn.Conv2d(32, 32, kernel_size=kernel_size, stride=2, padding=1),
            # nn.BatchNorm2d(32),
            # nn.LeakyReLU(),
            nn.Conv2d(32, 64, kernel_size=kernel_size, stride=2, padding=1),
            # nn.BatchNorm2d(64),
            # nn.LeakyReLU(),
            nn.Conv2d(64, 128, kernel_size=kernel_size, stride=2, padding=1),
            # nn.BatchNorm2d(128),
            # nn.LeakyReLU(),
        )
        self.encoder_lin = nn.Linear(
            int((256 / (2**4)) * (64 / (2**4)) * 128), encoded_dim
        )

        self.flatten = nn.Flatten(start_dim=1)

    def forward(self, x: Tensor):
        x = self.encoder(x)
        x = self.flatten(x)
        return self.encoder_lin(x)


class Decoder(pl.LightningModule):
    def __init__(self, kernel_size, encoded_dim):
        super().__init__()

        self.decoder_lin = nn.Linear(
            encoded_dim, int((256 / (2**4)) * (64 / (2**4)) * 128)
        )

        self.unflatten = nn.Unflatten(dim=1, unflattened_size=(128, 8, 8))

        self.decoder = nn.Sequential(
            nn.ConvTranspose2d(
                128, 64, kernel_size=kernel_size, stride=2, padding=1, output_padding=1
            ),
            # nn.BatchNorm2d(64),
            # nn.LeakyReLU(),
            nn.ConvTranspose2d(
                64, 32, kernel_size=kernel_size, stride=2, padding=1, output_padding=1
            ),
            # nn.BatchNorm2d(32),
            # nn.LeakyReLU(),
            nn.ConvTranspose2d(
                32, 32, kernel_size=kernel_size, stride=2, padding=1, output_padding=1
            ),
            # nn.BatchNorm2d(32),
            # nn.LeakyReLU(),
            nn.ConvTranspose2d(
                32, 1, kernel_size=kernel_size, stride=2, padding=1, output_padding=1
            ),
        )

    def forward(self, x: Tensor):
        x = self.decoder_lin(x)
        x = self.unflatten(x)
        return self.decoder(x)


class AutoEncoder(pl.LightningModule):
    def __init__(self, kernel_size, encoded_dim):
        super().__init__()

        self.encoder = Encoder(kernel_size, encoded_dim)
        self.decoder = Decoder(kernel_size, encoded_dim)
        self.fc = nn.Linear(
            int(((256 / (2**5)) ** 2) * 256), int(((256 / (2**5)) ** 2) * 256)
        )

    def forward(self, x):
        x = self.encoder(x)
        # x = self.fc(x)
        x = self.decoder(x)
        return x

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=3e-4)

    def training_step(self, batch: Tensor, batch_idx):
        reconstructions: Tensor = self(batch)
        if self.global_step % 200 == 0:
            spectrogram_to_image(batch[0].detach().cpu().numpy(), "autoencoder_input")
            spectrogram_to_image(
                reconstructions[0][0].detach().cpu().numpy(), "autoencoder_output"
            )
        loss = F.mse_loss(rearrange(reconstructions, "b c w h -> (c b) w h"), batch)

        if self.global_step % 5 == 0:
            self.log("train_loss", loss)

        return loss

    def validation_step(self, batch: Tensor, batch_idx):
        reconstructions: Tensor = self(batch)
        print(reconstructions.shape, batch.shape)
        loss = F.mse_loss(rearrange(reconstructions, "b c w h -> (c b) w h"), batch)
        self.log("val_loss", loss)

        return loss
