import os
import pathlib
import warnings

import hydra
import torch
from hydra.core.config_store import ConfigStore
import pytorch_lightning as pl
from pytorch_lightning import callbacks
from pytorch_lightning.loggers.tensorboard import TensorBoardLogger
import albumentations as A

from dataset import PLATE_ALPHABET, OCRDetectionDataModule
from model import OCRDetectorTrainer, CRNN
from config_data import TrainConfig
from transforms import get_ocr_valid_transform_list, AugTransform


cs = ConfigStore()
cs.store("train_detector", node=TrainConfig)


def get_train_transform():
    aug_transforms = [
        A.OneOf([
            A.Perspective(fit_output=False, scale=(0.015, 0.1), pad_val=(255, 255, 255)),
            A.Affine(scale={"x": (0.2, 1.5), "y": (0.3, 1.5)}, fit_output=True, p=0.65)
        ]),
        A.ColorJitter(brightness=0.8, contrast=0.1, p=0.75),
        A.ISONoise(intensity=[1, 1], p=0.15),
        A.MotionBlur(blur_limit=10),
        A.RandomShadow(p=0.25),
        A.RandomSunFlare(src_radius=20),
    ]
    return AugTransform(A.Compose(aug_transforms + get_ocr_valid_transform_list()))


def get_valid_transform():
    return AugTransform(A.Compose(get_ocr_valid_transform_list()))


def get_model():
    model = CRNN(PLATE_ALPHABET)
    return model


@hydra.main(config_name="train_detector")
def main(train_config: TrainConfig):
    os.chdir(hydra.utils.get_original_cwd())

    pl.seed_everything(train_config.seed)

    model = get_model()
    train_tranaforms = get_train_transform()
    valid_trasnforms = get_valid_transform()

    data = OCRDetectionDataModule(image_dir=train_config.ocr.image_dir,
                                  train_batch_size=train_config.ocr.train_batch_size,
                                  valid_batch_size=train_config.ocr.valid_batch_size,
                                  seed=train_config.seed,
                                  train_size=train_config.train_size,
                                  num_workers=train_config.num_workers,
                                  train_transforms=train_tranaforms,
                                  val_transforms=valid_trasnforms)

    exp_dir = pathlib.Path(train_config.exp_dir)

    checkpoint_dir = exp_dir / "checkpoint"
    checkpoint_dir.mkdir(exist_ok=True, parents=True)

    target_metric_name = "12"

    train_module = OCRDetectorTrainer(model=model,
                                      optimizer_config=train_config.optimizer,
                                      scheduler_config=train_config.scheduler,
                                      target_metric=target_metric_name)

    checkpoint_callback = callbacks.ModelCheckpoint(monitor=target_metric_name,
                                                    dirpath=checkpoint_dir,
                                                    filename=f"{{step}}-{{{target_metric_name}:.4f}}",
                                                    verbose=True,
                                                    save_last=True,
                                                    every_n_train_steps=train_config.val_check_interval + 1,
                                                    save_top_k=2,
                                                    mode="max",
                                                    save_weights_only=False)

    lr_monitor = callbacks.LearningRateMonitor(logging_interval='step')

    log_dir = exp_dir / "logs"
    log_dir.mkdir(exist_ok=True, parents=True)

    logger = TensorBoardLogger(str(log_dir))

    gpus = -1 if torch.cuda.is_available() else None

    if gpus is None:
        warnings.warn("GPU is not available. Try train on CPU. It may will bew very slow")

    trainer = pl.Trainer(amp_backend="native",
                         gpus=gpus,
                         logger=logger,
                         auto_select_gpus=True,
                         benchmark=True,
                         check_val_every_n_epoch=train_config.check_val_every_n_epoch,
                         flush_logs_every_n_steps=train_config.flush_logs_every_n_steps,
                         default_root_dir=str(exp_dir),
                         deterministic=False,
                         fast_dev_run=train_config.fast_dev_run,
                         progress_bar_refresh_rate=5,
                         precision=train_config.precision,
                         max_epochs=train_config.max_epochs,
                         val_check_interval=train_config.val_check_interval,
                         callbacks=[checkpoint_callback, lr_monitor]
                         )

    trainer.fit(train_module, datamodule=data)


if __name__ == "__main__":
    main()