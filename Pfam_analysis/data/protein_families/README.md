#Read me 

To use the script in the annotate_data, you have to run jackhmmer separately. Follow instructions from the Eddy Lab Manual

Then in command prompt enter:
jackhmmer -N 1 -E 1e-35 -o output.txt ref_seq.fasta ref_family.alignment.full

Pass the generated text file as output to this program
