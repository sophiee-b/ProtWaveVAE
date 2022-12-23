#!/usr/bin/env sh

python -V
export DIR="$(dirname "$(pwd)")"
#source activate torch_GPU
export PYTHONPATH=${PYTHONPATH}:${DIR}




# ===========================
# = For compute levenshtein =
# ===========================

export SEED=42
export design_path='../.././outputs/prediction/pfam/S1A/S1A_sample_sequences.csv'
export data_path='../.././data/protein_families/S1A/pfam_S1A.csv'
export des_seq_column='sequence'
export dataset_seq_column='Unaligned_Sequence'
export output_path=${design_path}
export option=1


python ../../compute_min_levenshtein.py \
	--SEED ${SEED} \
	--design_path ${design_path} \
	--data_path ${data_path} \
	--des_seq_column ${des_seq_column} \
	--dataset_seq_column ${dataset_seq_column} \
	--output_path ${output_path} \
	--option ${option} \



	









