"""

author: Niksa Praljak
"""
import torch
from torch import nn, optim
from torch.utils.data import DataLoader, Dataset
from torchvision import datasets, transforms
from torchvision.utils import save_image
from torch.autograd import Variable
from torch.nn import functional as F

# super Pytorch Lightning
import pytorch_lightning as pl
from pytorch_lightning import Trainer, seed_everything
from pytorch_lightning.callbacks import EarlyStopping

import source.preprocess as prep
import source.wavenet_decoder as wavenet
import source.model_components as model_comps
import source.PL_wrapper as PL_wrapper

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import math
import sys
import argparse
from tqdm import tqdm
import random
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import train_test_split
from scipy.stats import pearsonr, spearmanr

"""
Summary: train model session
"""

def get_args() -> any:

    # write output path name
    parser = argparse.ArgumentParser()
    
    # path varibles
    parser.add_argument('--dataset_path', default='./data/CV/ACS_SynBio_SH3_dataset.csv')
    parser.add_argument('--output_results_path', default='./outputs/SH3_task/CV/ProtWaveVAE_SSTrainingHist.csv')
    parser.add_argument('--output_model_path', default='./outputs/SH3_task/CV/ProtWaveVAE_SSTrainingHist.pth')
    parser.add_argument('--folder_path', default='./outputs/SH3_task/CV')

    # model training variables
    parser.add_argument('--SEED', default=42, type=int, help='Random seed')
    parser.add_argument('--batch_size', default=512, type=int, help='Size of the batch.')
    parser.add_argument('--epochs', default=1000, type=int, help='Number of epochs')
    parser.add_argument('--lr', default=1e-4, type=float, help='Learning rate')
    parser.add_argument('--DEVICE', default='cuda', help='Learning rate')
    parser.add_argument('--dataset_split', default=1, type=int, help='Choose whether to split into train/valid sets')
   
    # general architecture variables
    parser.add_argument('--z_dim', default=6, type=int, help='Latent space size')
    parser.add_argument('--num_classes', default=2, type=int, help='functional/nonfunctional labels')
    parser.add_argument('--aa_labels', default=21, type=int, help='AA plus pad gap (20+1) labels')
    
    # encoder hyperparameters
    parser.add_argument('--encoder_rates', default=5, type=int, help='dilation convolution depth')
    parser.add_argument('--C_in', default=21, type=int, help='input feature depth')
    parser.add_argument('--C_out', default=256, type=int, help='output feature depth')
    parser.add_argument('--alpha', default=0.1, type=float, help='leaky Relu hyperparameter (optional)')
    parser.add_argument('--enc_kernel', default=3, type=int, help='kernel filter size')
    parser.add_argument('--num_fc', default=1, type=int, help='number of fully connect layers')
      
    # top model (discriminative decoder) hyperparameters
    parser.add_argument('--disc_num_layers', default=2, type=int, help='depth of the discrim. top model')
    parser.add_argument('--hidden_width', default=10, type=int, help='width of top model')
    parser.add_argument('--p', default=0.3, type=float, help='top model dropout')

    # decoder wavenet hyperparameters
    parser.add_argument('--wave_hidden_state', default=256, type=int, help='no. filters for the dilated convolutions')
    parser.add_argument('--head_hidden_state', default=128, type=int, help='no. filters for the WaveNets top model')
    parser.add_argument('--num_dil_rates', default=8, type=int, help='depth of the WaveNet')
    parser.add_argument('--dec_kernel_size', default=3, type=int, help='WaveNet kernel size')

    # loss prefactor weights
    parser.add_argument('--nll_weight', default=1., type=float, help='NLL prefactor weight')
    parser.add_argument('--MI_weight', default=0.95, type=float, help='MI prefactor weight')
    parser.add_argument('--lambda_weight', default=2., type=float, help='MMD prefactor weight')
    parser.add_argument('--gamma_weight', default=1., type=float, help='discriminative prefactor weight')
        
    # cross-validation
    parser.add_argument('--K', default=5, type=int, help='number of CV splits')
    
    args = parser.parse_args()
    
    return args


def set_GPU() -> None:

    if torch.cuda.is_available():
        print('GPU available')
    else:
        print('Please enable GPU or use CUP')
        quit()

    USE_CUDA = True
    DEVICE = 'cuda' if USE_CUDA else 'cpu'
    return

def set_SEED(args: any) -> None:
    seed_everything(args.SEED, workers = True)
    return 

def prepare_data(
        args: any,
        train: any,
        test: any,
        num_workers: int=4
    ) -> (
        DataLoader,
        DataLoader,
        int,
        Dataset,
        Dataset
    ):


    # split data
    train_X, train_pheno, train_C, train_accept = train
    test_X, test_pheno, test_C, test_accept = test

   
    # train dataset
    train_dataset = prep.SH3_dataset(
                            onehot_inputs=train_X,
                            re_inputs=train_pheno,
                            C_inputs=train_C,
                            accept_inputs=train_accept
    )
    train_dataloader = DataLoader(
                        train_dataset,
                        batch_size=args.batch_size,
                        num_workers=8,
                        shuffle=True
    )

    # valid dataset
    test_dataset = prep.SH3_dataset(
                            onehot_inputs=test_X,
                            re_inputs=test_pheno,
                            C_inputs=test_C,
                            accept_inputs=test_accept
    )
    test_dataloader = DataLoader(
                        test_dataset,
                        batch_size=args.batch_size,
                        num_workers=8,
                        shuffle=False
    )


    # extract the sequence length
    train_dataset_size, max_protein_len, _ = train_X.shape
    print('Size of the training dataset:', train_dataset_size)

    return (
            train_dataloader,
            test_dataloader,
            max_protein_len,
            train_dataset,
            test_dataset
    )


def get_model(
        args: any,
        protein_len: int
    ) -> any:

        

    # define inference model:
    encoder = model_comps.GatedCNN_encoder(
                                  protein_len=protein_len,
                                  class_labels=args.aa_labels,
                                  z_dim=args.z_dim,
                                  num_rates=args.encoder_rates,
                                  C_in=args.C_in,
                                  C_out=args.C_out,
                                  alpha=args.alpha,
                                  kernel=args.enc_kernel,
                                  num_fc=args.num_fc,
    )
    # define regression model:
    decoder_re = model_comps.Decoder_re(
                                    num_layers=args.disc_num_layers,
                                    hidden_width=args.hidden_width,
                                    z_dim=args.z_dim,
                                    num_classes=args.num_classes,
                                    p=args.p
    )
  
    # define generator model:
    decoder_wave = wavenet.Wave_generator(
                                protein_len=protein_len,
                                class_labels=args.aa_labels,
                                DEVICE=args.DEVICE,
                                wave_hidden_state=args.wave_hidden_state,
                                head_hidden_state=args.head_hidden_state,
                                num_dil_rates=args.num_dil_rates,
                                kernel_size=args.dec_kernel_size
    )

    # latent global conditioning
    cond_mapper = wavenet.CondNet(
                                z_dim=args.z_dim, 
                                output_shape=(1, protein_len)
    )

    # define final model configuration ...
    # torch model
    SS_model = model_comps.SS_InfoVAE(
                             DEVICE=args.DEVICE,
                             encoder=encoder,
                             decoder_recon=decoder_wave,
                             cond_mapper=cond_mapper,
                             decoder_pheno=decoder_re,
                             z_dim=args.z_dim
    )
    
    # pytorch model
    PL_model = PL_wrapper.Lit_SSInfoVAE(
                            DEVICE=args.DEVICE,
                            SS_InfoVAE=SS_model,
                            xi_weight=args.nll_weight,
                            alpha_weight=args.MI_weight,
                            lambda_weight=args.lambda_weight,
                            gamma_weight=args.gamma_weight,
                            lr=args.lr,
                            z_dim=args.z_dim
    )

    return PL_model




def train_model(
        args: any,
        train_dataloader: DataLoader,
        test_dataloader: DataLoader,
        PL_model: pl.LightningModule
    ) -> (
            any,
            pd.Series
    ):
        
        trainer = pl.Trainer(
         logger=False,
         callbacks=None,
         max_epochs=args.epochs,
         gpus = 1 if torch.cuda.is_available() else None,
         )      
        
        if args.dataset_split == 1:
            print('\nTrain/Valid split training\n')
            trainer.fit(PL_model, train_dataloaders=train_dataloader, val_dataloaders=test_dataloader)  
        else:
            print('\nTrain on the whole data\n')
            trainer.fit(PL_model, train_dataloaders=train_dataloader, val_dataloaders=test_dataloader)

        train_L = trainer.callback_metrics['L_train_epoch'].item()
        train_NLL = trainer.callback_metrics['L_nll_train_epoch'].item()
        train_kld = trainer.callback_metrics['L_kld_train_epoch'].item()
        train_mmd = trainer.callback_metrics['L_mmd_train_epoch'].item()
        train_pheno = trainer.callback_metrics['L_pheno_train_epoch'].item()
        train_precision_epoch = trainer.callback_metrics['Train_precision_epoch'].item()
        train_recall_epoch = trainer.callback_metrics['Train_recall_epoch'].item()
        train_f1_epoch = trainer.callback_metrics['Train_f1_epoch'].item()
     
        val_L = trainer.callback_metrics['L_valid_epoch'].item()
        val_NLL = trainer.callback_metrics['L_nll_valid_epoch'].item()
        val_kld = trainer.callback_metrics['L_kld_valid_epoch'].item()
        val_mmd = trainer.callback_metrics['L_mmd_valid_epoch'].item()
        val_pheno = trainer.callback_metrics['L_pheno_valid_epoch'].item()
        val_precision_epoch = trainer.callback_metrics['val_precision_epoch'].item()
        val_recall_epoch = trainer.callback_metrics['val_recall_epoch'].item()
        val_f1_epoch = trainer.callback_metrics['val_f1_epoch'].item()

        final_epoch_results = [
            train_L,
            val_L,
            train_NLL,
            train_kld,
            train_mmd,
            train_pheno,
            train_precision_epoch,
            train_recall_epoch,
            train_f1_epoch,
            val_NLL,
            val_kld,
            val_mmd,
            val_pheno,
            val_precision_epoch,
            val_recall_epoch,
            val_f1_epoch
        ]
        
        all_epochs_losses = {
            'train_L': list(),
            'train_nll': list(),
            'train_kld': list(),
            'train_mmd': list(),
            'train_pheno': list(),
            'train_precision': list(),
            'train_recall': list(),
            'train_f1': list(),
            'valid_L': list(),
            'valid_nll': list(),
            'valid_kld': list(),
            'valid_mmd': list(),
            'valid_pheno': list(),
            'valid_precision': list(),
            'valid_recall': list(),
            'valid_f1': list()
        }
            
        # allocate losses from all of the epochs:
        all_epochs_losses['train_L'] = PL_model.L_train_list + [float('nan')]
        all_epochs_losses['train_nll'] = PL_model.L_train_nll_list + [float('nan')]
        all_epochs_losses['train_kld'] = PL_model.L_train_kld_list + [float('nan')]
        all_epochs_losses['train_mmd'] = PL_model.L_train_mmd_list + [float('nan')]
        all_epochs_losses['train_pheno'] = PL_model.L_train_pheno_list + [float('nan')]
        all_epochs_losses['train_precision'] = PL_model.train_precision_list + [float('nan')]
        all_epochs_losses['train_recall'] = PL_model.train_recall_list + [float('nan')]
        all_epochs_losses['train_f1'] = PL_model.train_f1_list + [float('nan')]

        all_epochs_losses['valid_L'] = PL_model.L_val_list
        all_epochs_losses['valid_nll'] = PL_model.L_val_nll_list
        all_epochs_losses['valid_kld'] = PL_model.L_val_kld_list
        all_epochs_losses['valid_mmd'] = PL_model.L_val_mmd_list
        all_epochs_losses['valid_pheno'] = PL_model.L_val_pheno_list
        all_epochs_losses['valid_precision'] = PL_model.val_precision_list
        all_epochs_losses['valid_recall'] = PL_model.val_recall_list
        all_epochs_losses['valid_f1'] = PL_model.val_f1_list

        return (
                PL_model,
                final_epoch_results,
                all_epochs_losses
        )


def save_results(
        args: any,
        final_epoch_results: list,
        all_epochs_losses: dict,
        CV_index: int

    ) -> None:
    
    # save results
    # final epoch losses
    final_epoch_columns = [
                    'train_L',
                    'val_L',
                    'train_NLL',
                    'train_kld',
                    'train_mmd',
                    'train_pheno',
                    'train_precision',
                    'train_recall',
                    'train_f1',
                    'val_NLL',
                    'val_kld',
                    'val_mmd',
                    'val_pheno',
                    'val_precision',
                    'val_recall',
                    'val_f1'
    ]               
    final_epoch_dict = dict(map(lambda column, data : (column, [data]), final_epoch_columns, final_epoch_results))
    final_epoch_df = pd.DataFrame(final_epoch_dict)
    final_epoch_df.to_csv(args.output_results_path, index = False)

    
    # save all epoch results
    all_epochs_df = pd.DataFrame(all_epochs_losses)
    all_epochs_df.to_csv(args.output_results_path.replace('.csv', f'[CV_index={CV_index}]_all.csv'), index = False)
    
    # save model
   # torch.save(PL_model.model.state_dict(), args.output_model_path)

    return


def CV_train(
        args: any
    ) -> None:


    from sklearn.model_selection import KFold, StratifiedShuffleSplit
    
    sss = StratifiedShuffleSplit(n_splits = args.K)
    
    # get dataframe
    df = pd.read_csv(args.dataset_path)
    max_seq_len = max([len(seq.replace('-','')) for seq in df.Sequences_unaligned.values])

    X_OH, y_pheno, y_C, y_accept = prep.prepare_SH3_data(
            df=df,
            max_seq_len=max_seq_len
    )

    for ii, (train_index, test_index) in enumerate(sss.split(X_OH, y_C)):

        # split data into train/test
        X_train, X_test = X_OH[train_index], X_OH[test_index]
        y_pheno_train, y_pheno_test = y_pheno[train_index], y_pheno[test_index]
        y_C_train, y_C_test = y_C[train_index], y_C[test_index]
        y_accept_train, y_accept_test = y_accept[train_index], y_accept[test_index]


        train_dataloader, test_dataloader, max_seq_len, train_dataset, test_dataset  = prepare_data(
                args=args,
                train=(X_train, y_pheno_train, y_C_train, y_accept_train),
                test=(X_test,y_pheno_test,y_C_test,y_accept_test),
                num_workers=4,
        )
        
        # acquire model
        PL_model = get_model(
                args=args,
                protein_len=max_seq_len
        )
        print('Start training!')
        # train model
        PL_model, final_epoch_results, all_epochs_losses = train_model(
                                                                args=args,
                                                                PL_model=PL_model,
                                                                train_dataloader=train_dataloader,
                                                                test_dataloader=test_dataloader
        )
        print('Finished training!')
        
        # save spreadsheets
        save_results(
            args=args,
            final_epoch_results=final_epoch_results,
            all_epochs_losses=all_epochs_losses,
            CV_index=ii
        )
        print('Save results ...')

    return


if __name__ == '__main__':


    # inpurt parameters, variables, and paths
    args = get_args()
    # create output folder
    os.makedirs(args.folder_path, exist_ok=True)
    # set GPU
    set_GPU()
    # set seed for reproducibility 
    set_SEED(args=args)
    # Cross-validate
    CV_train(
            args=args
    )

