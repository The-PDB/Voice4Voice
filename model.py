import math
from turtle import forward
from typing import Tuple

import torch
from torch import nn, Tensor
import torch.nn.functional as F
from rich import print
from einops.layers.torch import Rearrange
import pytorch_lightning as pl
from utils import audio_to_spectrogram, spectrogram_to_image, spectrogram_to_audio


class PatchEmbedding(pl.LightningModule):
    def __init__(self, patch_size, d_model):
        super().__init__()

        self.projection = nn.Sequential(
            nn.Conv2d(
                1,
                d_model,
                kernel_size=(patch_size, d_model),
                stride=(patch_size, d_model),
            ),
            Rearrange("b e h w -> b (w h) e")
            # Rearrange("b h (w s) -> b (h w) s", s=patch_size),
            # nn.Linear(patch_size * d_model, d_model),
        )

    def forward(self, x: Tensor) -> Tensor:
        # print(x.shape)
        # print(self.projection(x).shape)
        return self.projection(x.unsqueeze(1))


class PositionalEncoding(pl.LightningModule):
    dropout: nn.Dropout
    pos_encoding: Tensor

    def __init__(self, dim_model: int, dropout_p: float, max_len: int):
        super().__init__()
        # Modified version from: https://pytorch.org/tutorials/beginner/transformer_tutorial.html
        # max_len determines how far the position can have an effect on a token (window)

        # Info
        if dim_model % 2 != 0:
            dim_model += 1
        self.dim_model = dim_model
        self.dropout = nn.Dropout(dropout_p)
        # print(max_len, self.dim_model)
        # Encoding - From formula
        pos_encoding = torch.zeros(max_len, dim_model)
        positions_list = torch.arange(0, max_len, dtype=torch.float).view(-1, 1)
        # 0, 1, 2, 3, 4, 5
        division_term = torch.exp(
            torch.arange(0, dim_model, 2).float() * (-math.log(10000.0)) / dim_model
        )  # 1000^(2i/dim_model)
        # print(positions_list.shape, division_term.shape)
        # PE(pos, 2i) = sin(pos/1000^(2i/dim_model))
        pos_encoding[:, 0::2] = torch.sin(positions_list * division_term)

        # PE(pos, 2i + 1) = cos(pos/1000^(2i/dim_model))
        pos_encoding[:, 1::2] = torch.cos(positions_list * division_term)

        self.register_buffer(
            "pos_encoding", pos_encoding
        )  # Makes positional encoding apart of model state_dict

    def forward(self, token_embedding: Tensor) -> Tensor:
        # print(token_embedding.shape, self.pos_encoding.shape)
        return self.dropout(
            token_embedding * math.sqrt(self.dim_model)
            + self.pos_encoding[:, : token_embedding.shape[-1]]
        )


class TransformerModel(pl.LightningModule):
    """
    An object containing the architecture for a "Spec2Spec" transformer model. Inherits pl.LightningModule.

    Attributes
    ----------
    d_model: int
        The dimensionality of the model.
    ntoken: int
        The number of tokens the model ingests.
    tgt_mask: Torch DERPECITATED MOVED AROUDN REWRTIE YES
        Masks out the future things fron the multiheaded masked attention in the decoder. It's a triangular matrix.
    model_type: str

    """

    d_model: int
    ntoken: int
    model_type: str = "Transformer"
    lr: float
    prenet: nn.Sequential
    pos_embedding: nn.Embedding
    transformer: nn.Transformer
    pos_emb_residual: PositionalEncoding

    def __init__(
        self,
        config: dict,
        ntoken: int,
        d_model: int,
    ):
        super().__init__()
        self.save_hyperparameters()
        self.d_model = d_model
        self.ntoken = ntoken

        self.lr = config["lr"]
        self.patch_size = config["patch_size"]
        dropout = config["dropout"]
        nhead = config["nhead"]
        nlayers = config["nlayers"]
        leakyness = config["leaky"]

        self.start_token_embedding = nn.Linear(1, d_model)

        # self.prenet = nn.Sequential(
        #     nn.Dropout(dropout),
        #     nn.Linear(d_model, d_model),
        #     nn.LeakyReLU(leakyness),
        #     nn.Dropout(dropout),
        #     nn.Linear(d_model, d_model),
        #     nn.LeakyReLU(leakyness),
        # )
        # self.patch_embedding = PatchEmbedding(self.patch_size, d_model)
        # self.pos_embedding = nn.Embedding(ntoken, d_model)

        self.transformer = nn.Transformer(
            d_model=d_model,
            nhead=nhead,
            num_decoder_layers=nlayers,
            num_encoder_layers=nlayers,
            dropout=dropout,
            batch_first=True,
            activation=nn.LeakyReLU(leakyness),
        )

        self.mel_linear = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(d_model, d_model),
        )

        self.stop_linear = nn.Sequential(
            nn.Dropout(dropout), nn.Linear(d_model, 1), nn.Sigmoid()
        )

        # self.fixed_pos_embedding = PositionalEncoding(ntoken, d_model)
        self.pos_emb_residual = PositionalEncoding(d_model, dropout, ntoken)

    def forward(
        self, src: Tensor, tgt: Tensor, tgt_mask: Tensor
    ) -> Tuple[Tensor, Tensor]:
        src.to(self.device)
        tgt.to(self.device)

        # src = self.prenet(src)
        # tgt = self.prenet(tgt)
        # print(src.shape, tgt.shape)
        # src = self.patch_embedding(src)  # 4, 8576, 1
        # tgt = self.patch_embedding(tgt)

        tgt = torch.cat(
            (
                self.start_token_embedding(
                    torch.ones(tgt.shape[0], 1, 1, device=self.device)
                ),
                tgt,
            ),
            dim=1,
        )

        src = self.pos_emb_residual(src)
        tgt = self.pos_emb_residual(tgt)

        out = self.transformer(src, tgt, tgt_mask=tgt_mask)

        return self.mel_linear(out)  # , self.stop_linear(out)

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=self.lr)

    def get_tgt_mask(
        self, size
    ) -> torch.Tensor:  # For now its fine having self attention be applied to padding, though should fix in future. Meaning this method should take in the (model sequence length) and the (audio clip sequence length) as paremeters
        mask = torch.tril(torch.ones(size, size, device=self.device) == 1)
        mask = mask.float()
        mask = mask.masked_fill(mask == 0, float("-inf"))
        mask = mask.masked_fill(mask == 1, 0.0)

        return mask

    def training_step(
        self, batch, batch_idx
    ):  # Find better naming convention (output or encoder or target input)
        loss = self.run_model(batch)
        # + F.binary_cross_entropy(
        #     predicted_stops, target_stops
        # )
        if self.global_step % 5 == 0:
            self.log("train_loss", loss)
        # if self.global_step % 50 == 0:
        #     a = predicted_specs[0].cpu().detach().numpy()
        #     b = output_tensors[0].cpu().detach().numpy()
        #     spectrogram_to_image(a, "predicted")
        #     spectrogram_to_image(b, "target")
        #     spectrogram_to_audio(a, "predicted_audio.wav", 128, 44100)
        #     spectrogram_to_audio(b, "target_audio.wav", 128, 44100)
        return loss  # https://stackoverflow.com/questions/53994625/how-can-i-process-multi-loss-in-pytorch
        # Weight postivie stop token

    def validation_step(self, batch, batch_idx):
        loss = self.run_model(batch)
        self.log("val_loss", loss)

        return loss

    def run_model(self, batch):
        input_tensors, output_tensors = batch

        predicted_specs = self(
            input_tensors,
            output_tensors[:, :-1],
            self.get_tgt_mask(self.ntoken),
        )

        # predicted_stops = predicted_stops.squeeze(2)
        # predicted_specs, predicted_stops = torch.stack(
        #     [
        #         spec.masked_fill(specs_clipping_masks[i], 0)
        #         for i, spec in enumerate(predicted_specs)
        #     ]
        # ).to(self.device), torch.stack(
        #     [
        #         stop.masked_fill(stops_clipping_masks[i], 0)
        #         for i, stop in enumerate(predicted_stops)
        #     ]
        # ).to(
        #     self.device
        # )

        return F.mse_loss(
            predicted_specs,
            output_tensors,  # reduction="sum"
        )  # + F.binary_cross_entropy(predicted_stops, target_stops, reduction="sum")
