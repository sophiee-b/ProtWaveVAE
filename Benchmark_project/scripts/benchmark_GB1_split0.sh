#!/usr/bin/env sh

python -V
export DIR="$(dirname "$(pwd)")"
#source activate torch_GPU
export PYTHONPATH=${PYTHONPATH}:${DIR}


# path variables
export data_path='.././data/GB1/four_mutations_full_data.csv'
export train_path='.././data/GB1/four_mutations_full_data.csv'
export valid_path='../.data/GB1/four_mutations_full_data.csv'
export output_results_path='.././outputs/benchmark_task/final_model/GB1/final_model_split0.csv'
export output_model_path='.././outputs/benchmark_task/final_model/GB1/final_model_split0.pth'
export output_folder_path='.././outputs/benchmark_task/final_model/GB1'
export protein='GB1'

# model training variables
export SEED=42
export batch_size=512
export epochs=2000
export lr=1e-5
export DEVICE='cuda'
export split_option=0


# general architecture variables
export z_dim=4
export num_classes=1

# encoder hyperparameters
export encoder_rates=0
export C_in=21
export C_out=128
export alpha=0.1 # might not be necessary (Only for leaky relu)
export enc_kernel=3
export num_fc=2

# top model (discriminative decoder) hyperparameters
export disc_num_layers=2
export hidden_width=100
export p=0.1

# decoder wavenet hyperparameters
export wave_hidden_state=128
export head_hidden_state=128
export num_dil_rates=8
export dec_kernel_size=3
export aa_labels=21

# loss prefactor
export nll_weight=1.0
export MI_weight=0.99
export lambda_weight=2.0
export gamma_weight=1.0


python ../train_SemiSupervised.py \
		--data_path ${data_path} \
		--output_results_path ${output_results_path} \
		--output_model_path ${output_model_path} \
		--output_folder_path ${output_folder_path} \
		--protein ${protein} \
		--SEED ${SEED} \
		--batch_size ${batch_size} \
                --epochs ${epochs} \
		--lr ${lr} \
		--DEVICE ${DEVICE} \
		--split_option ${split_option} \
		--z_dim ${z_dim} \
		--num_classes ${num_classes} \
                --encoder_rates ${encoder_rates} \
		--C_in ${C_in} \
		--C_out ${C_out} \
		--alpha ${enc_kernel} \
		--num_fc ${num_fc} \
		--disc_num_layers ${disc_num_layers} \
		--hidden_width ${hidden_width} \
		--p ${p} \
		--wave_hidden_state ${wave_hidden_state} \
                --head_hidden_state ${head_hidden_state} \
                --num_dil_rates ${num_dil_rates} \
                --dec_kernel_size ${dec_kernel_size} \
                --aa_labels ${aa_labels} \
		--nll_weight ${nll_weight} \
                --MI_weight ${MI_weight} \
                --lambda_weight ${lambda_weight} \
                --gamma_weight ${gamma_weight} \
		













	








