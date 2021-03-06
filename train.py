from pathlib import Path

import hydra
import pandas as pd
import pytorch_lightning as pl
from easydict import EasyDict
from omegaconf import DictConfig
from pytorch_lightning.callbacks import ModelCheckpoint, LearningRateMonitor
from pytorch_lightning.loggers import WandbLogger
from sklearn.model_selection import KFold

import wandb
from src.dataloader import ClassificationDataLoader
from src.model import BertModel

pl.seed_everything(0)


@hydra.main(config_path="configs", config_name="kaggle-gpu")
def train(cfg: DictConfig):
    cfg = EasyDict(cfg)

    data_dir = Path(cfg.data_dir)
    current_dir = Path(cfg.current_dir)
    train_df = pd.read_csv(data_dir / "train.csv")
    test_df = pd.read_csv(data_dir / "test.csv")

    folds = KFold(n_splits=5)

    predictions = []
    for ii, (train_indices, val_indices) in enumerate(folds.split(train_df)):
        train_data, val_data = train_df.iloc[train_indices], train_df.iloc[val_indices]
        test_predictions = train_model(cfg, train_data, val_data, test_df, fold_index=ii)
        predictions.append(test_predictions)

    final_submission = most_frequent_prediction(predictions)
    final_submission.to_csv(current_dir / "submission.csv", index=False)

    # Saving merged final predictions to wandb
    wandb.init(project="contradictory-my-dear-watson",
               group=cfg.exp_name,
               job_type="save final prediction")
    wandb.save(current_dir / "submission.csv")
    wandb.finish()

    print("training is finished!")


def train_model(cfg: DictConfig,
                train_data: pd.DataFrame,
                val_data: pd.DataFrame,
                test_data: pd.DataFrame,
                fold_index: int):
    model = BertModel(cfg)

    dataloader = ClassificationDataLoader(
        train_df=train_data.reset_index(drop=True),
        val_df=val_data.reset_index(drop=True),
        test_df=test_data,
        tokenizer=model.tokenizer,
        **cfg
    )

    current_dir = Path(cfg.current_dir)
    logger = WandbLogger(project="contradictory-my-dear-watson",
                         group=cfg.exp_name,
                         job_type="k-fold training",
                         name=f"fold={fold_index}",
                         save_dir=str(current_dir / "logs"),
                         log_model='all',
                         config=cfg)

    checkpointer = ModelCheckpoint(dirpath=current_dir / "models",
                                   monitor="val/acc_epoch",
                                   mode="min",
                                   save_weights_only=True,
                                   save_top_k=1,
                                   filename=r"bert_{epoch}-{val_acc_epoch:.02f}_fold=" + str(fold_index))

    lr_scheduler = LearningRateMonitor(logging_interval='step')

    trainer = pl.Trainer(
        logger=logger,
        callbacks=[checkpointer, lr_scheduler],
        gpus=cfg.gpus,
        max_epochs=cfg.num_epochs,
        default_root_dir=str(current_dir / "logs"),
        # limit_train_batches=2,
        # limit_val_batches=2,
    )

    trainer.fit(model, dataloader.train_dataloader(), dataloader.val_dataloader())

    # This will generate predictions for test.csv and save them to submission.csv file
    trainer.test(model, dataloader.test_dataloader())
    wandb.finish()

    fold_submission = pd.read_csv(current_dir / "submission.csv")
    return fold_submission


def most_frequent_prediction(fold_submissions: list) -> pd.DataFrame:
    sample_submission = fold_submissions[0].copy()

    results = {}
    for ii, df in enumerate(fold_submissions):
        results[ii] = df.prediction.to_list()

    # Returns most frequent values from each fold
    prediction = pd.DataFrame(results).mode(axis=1)[0].astype("int").to_list()
    sample_submission.prediction = prediction

    return sample_submission


if __name__ == '__main__':
    train()
