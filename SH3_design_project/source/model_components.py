"""
@Author: Niksa Praljak
"""

import torch
from torch import nn, optim
from torch.utils.data import DataLoader, Dataset
from torchvision import datasets, transforms
from torchvision.utils import save_image
from torch.autograd import Variable
from torch.nn import functional as F

from tqdm import tqdm

import numpy as np

"""
@summary: here, we are only running a simple MMD-VAE with Semi-supervised learning, however, the components (i.e., encoder+decoder) are 
quite sophisticated.

"""


# encoder component

class GatedCNN_encoder(nn.Module):

    def __init__(
            self,
            protein_len: int=100,
            class_labels: int=21,
            z_dim: int=6,
            num_rates: int=0,
            C_in: int=21,
            C_out: int=256,
            alpha: float=0.1,
            kernel: int=3,
            num_fc: int=1,
        ) -> None:



        super(GatedCNN_encoder, self).__init__()

        # define useful parameters:
        self.protein_len = protein_len
        self.aa_labels = class_labels
        self.z_dim = z_dim
        self.C_in = C_in
        self.C_out = C_out
        self.kernel_size = kernel
        self.num_fc = num_fc

        if num_rates == 0:
            self.num_rates = self.compute_max_enc_rate()
        else:
            self.num_rates = num_rates
   
        # initial embedding: convert from one-hot encodings to conv filter features
        self.initial_conv_blocks = nn.ModuleList()
        # signal and gate for the input sequences
        self.signal_convs = nn.ModuleList()
        self.gate_convs = nn.ModuleList()

        # initial convolutional feature embedding
        self.initial_conv_blocks.append(nn.Conv1d(self.C_in, self.C_out, kernel_size = 1, padding = 0, bias = True))
        nn.init.xavier_uniform_(self.initial_conv_blocks[0].weight)

        # batch norm
        self.batch_norms = nn.ModuleList()
        self.batch_norms.append(nn.BatchNorm1d(C_out))


        # dilation rates: 1, 2, 4, 8, 16, ... , 2^(num_rates - 1)
        dilation_rates = [2**ii for ii in range(self.num_rates)] # grow by power by 2


        for ii, dilation_rate in enumerate(dilation_rates):

            # signal and gate for the input sequences
            self.signal_convs.append(nn.Conv1d(self.C_out, self.C_out,
                                               kernel_size = 3,
                                               padding = 0,
                                               bias = False,
                                               dilation = dilation_rate)
                                    )

            nn.init.xavier_uniform_(self.signal_convs[ii].weight)
            # add batch norm after the signal operations

            self.gate_convs.append(nn.Conv1d(self.C_out, self.C_out,
                                               kernel_size = 3,
                                               padding = 0,
                                               bias = False,
                                               dilation = dilation_rate)
                                    )
            nn.init.xavier_uniform_(self.gate_convs[ii].weight)

            # add batch norm after the gated-conv
            self.batch_norms.append(nn.BatchNorm1d(C_out))

        # final conv operation
        self.final_conv_signal = nn.Conv1d(self.C_out, 1, kernel_size = 1, padding = 0, bias = False)
        nn.init.xavier_uniform_(self.final_conv_signal.weight)

        self.final_conv_gate = nn.Conv1d(self.C_out, 1, kernel_size = 1, padding = 0, bias = False)
        nn.init.xavier_uniform_(self.final_conv_gate.weight)


        # num of features outputted by the gated convolutional block
        output_size = (self.protein_len - 2**(self.num_rates-1) * 2 * ( self.kernel_size-1 ) ) + 2

        # add batch norm to the final conv gate
        self.batch_norms.append(nn.BatchNorm1d(1))

        
        # final fully connected layers       
        self.encoder_fully_connected = nn.ModuleList()
        
        for ii in range(self.num_fc):
           
           self.encoder_fully_connected.append( nn.Linear(output_size, output_size))
        
		
	# mean and variance of the amoritzed varitional approximation
        self.q_z_mean = nn.Linear(output_size, z_dim)
        self.q_z_var = nn.Sequential( 
                nn.Linear(output_size, z_dim),
                nn.Softplus()
        )

        # create nonlinear activation functions
        self.sigm = nn.Sigmoid()
        self.lrelu = nn.LeakyReLU(negative_slope = 0.1)
    
    @staticmethod
    def compute_Lout(
               L_in: int,
               dilation: int,
               kernel_size: int,
               padding: int=0,
               stride: int=1
        ) -> int:
        return (L_in + 2 * padding - dilation * (kernel_size - 1) - 1) / stride + 1
                 

    def compute_max_enc_rate(self,)-> int:
 
        # loop over all possible hyperparameter options
        for max_enc_dilation in range(1, 100):
            
            dilations = [2**ii for ii in range(max_enc_dilation)]
            L = self.protein_len # set the intial input sequence length
            
       
            try:
              for dil in dilations:

                 # output sequence length af ter convolution operation
                 L = GatedCNN_encoder.compute_Lout(
                                             L_in = L,
                                             dilation = dil,
                                             kernel_size = self.kernel_size
                 )

                 if L <= 0:
                    raise StopIteration

                 else:
                    pass
            
            except StopIteration:
              return max_enc_dilation - 1 
             
        return max_enc_dilation


    def forward(
            self,
            x: torch.FloatTensor
        ) -> (
                torch.FloatTensor,
                torch.FloatTensor
        ):
            # initial embedding
            x = self.initial_conv_blocks[0](x)
            x = self.batch_norms[0](x) # apply batch norm

            for ii in range(self.num_rates):
                # convolutional operation for the signal: tanh(W*X)
                signal = self.signal_convs[ii](x)

                # convolutional operation for the gate: sigm(W*X)
                gate = self.gate_convs[ii](x)

                # gated conv operation
                x = signal * self.sigm( gate )
                x = self.batch_norms[ii+1](x) # apply batch norm
                    
            # signal + gate for the final output of the gated-convolution block
            signal = self.final_conv_signal(x)
            gate = self.final_conv_gate(x)
            conv_out = signal * self.sigm(gate) # shape: (batch_size, 1, output_length)
            enc_out = self.batch_norms[-1](conv_out).squeeze(1)# apply batch norm
  
            for ii in range(self.num_fc):
                h = self.encoder_fully_connected[ii](enc_out)
                enc_output = self.lrelu(h)

            mu = self.q_z_mean(enc_out) # mean for the latent embedding
            var = self.q_z_var(enc_out) # variance for the latent embedding

            return (
                    mu,
                    var
            )

    def mode_prediction(self, x: torch.FloatTensor) -> (torch.FloatTensor):
        """
        equivalent to doing inf. samples from the encoder model since reparam uses a N(0,I) dist. 
        """
        # initial embedding
        x = self.initial_conv_blocks[0](x)
        x = self.batch_norms[0](x)

        for ii in range(self.num_rates):
            # conv operation for the signal: tanh(W*X)
            signal = self.signal_convs[ii](x)
            # conv operation for the gate: sigm(W*X)
            gate = self.gate_convs[ii](x)

            # gated conv operation
            x = signal * self.sigm(gate)
            x = self.batch_norms[ii+1](x)

        # signal + gate for the final output of the gated-conv block
        signal = self.final_conv_signal(x)
        gate = self.final_conv_gate(x)
        conv_out = signal * self.sigm(gate) # shape: (batch_size, 1, output_length)
        enc_out = self.batch_norms[-1](conv_out).squeeze(1)# apply batch norm
  
        for ii in range(self.num_fc):
            h = self.encoder_fully_connected[ii](enc_out)
            enc_out = self.lrelu(h)

        mu = self.q_z_mean(enc_out) # mean for the latent embedding
        
        return mu



# decoder for regression: p(y|z)

class TopModel_layer(nn.Module):

    def __init__(
            self,
            in_width: int,
            out_width: int,
            p: float = 0.1
    ):

        super(TopModel_layer,self).__init__()

        self.in_width = in_width
        self.out_width = out_width
        self.p = p
        self.hidden_layer = nn.Sequential(
                nn.Linear(self.in_width,self.out_width),
                nn.LayerNorm(self.out_width),
                nn.SiLU(),
                nn.Dropout(p = self.p)
        )

    def forward(self, x: torch.FloatTensor) -> torch.FloatTensor:
        return self.hidden_layer(x)


class Decoder_re(nn.Module):

    def __init__(
            self,
            num_layers: int,
            hidden_width: int,
            z_dim: int,
            num_classes: int,
            p: float=0.1
        ):
        super(Decoder_re,self).__init__()

        self.num_layers = num_layers
        self.hidden_width = hidden_width
        self.z_dim = z_dim
        self.num_classes = num_classes
        self.p = p

        self.reg_model_layers = nn.ModuleList()
        self.class_model_layers = nn.ModuleList()

        for ii in range(num_layers):

            if ii == 0:
                
                # regression model
                self.reg_model_layers.append(
                        TopModel_layer(
                            in_width=self.z_dim,
                            out_width=self.hidden_width,
                            p=self.p
                        )
                )
                
                # classification model
                self.class_model_layers.append(
                        TopModel_layer(
                            in_width=self.z_dim,
                            out_width=self.hidden_width,
                            p=self.p
                        )
                )

            else:
 
                # regression model
                self.reg_model_layers.append(
                        TopModel_layer(
                            in_width=self.hidden_width,
                            out_width=self.hidden_width,
                            p=self.p
                        )
                )
                
                # classification model
                self.class_model_layers.append(
                        TopModel_layer(
                            in_width=self.hidden_width,
                            out_width=self.hidden_width,
                            p=self.p
                        )
                )



        self.output_class_layer = nn.Linear(self.hidden_width, self.num_classes)
        self.output_reg_layer = nn.Linear(self.hidden_width, 1)

        self.sigmoid = nn.Sigmoid()


    def reg_forward(self, z: torch.FloatTensor) -> torch.FloatTensor:

        for layer in self.reg_model_layers:
            z = layer(z)
        return self.output_reg_layer(z)


    def class_forward(self, z: torch.FloatTensor) -> torch.FloatTensor:

        for layer in self.class_model_layers:
            z = layer(z)
        return self.sigmoid(self.output_class_layer(z))


    def forward(self, z: torch.FloatTensor) -> (
            torch.FloatTensor,
            torch.FloatTensor
        ):

        reg_z = self.reg_forward(z)
        class_z = self.class_forward(z)

        return (
                reg_z,
                class_z
        )

        

# Create SS-InfoVAE arhictecture using components from above:

class SS_InfoVAE(nn.Module):
    """
    class description: This is the InfoVAE model. 

    """
    def __init__(
            self,
            DEVICE: str,
            encoder: any,
            decoder_recon: any,
            cond_mapper: any,
            decoder_pheno: any,
            z_dim: int = 6
        ):
        super(SS_InfoVAE, self).__init__()

        # model components:
        self.inference = encoder
        self.generator = decoder_recon
        self.cond_mapper = cond_mapper
        self.discriminator = decoder_pheno
        self.DEVICE = DEVICE

        # additional components:
        self.softmax = nn.Softmax(dim = -1)
        
        # hyperparameters
        self.z_dim = z_dim
	
    def reparam_trick(
            self,
            mu: torch.FloatTensor,
            var: torch.FloatTensor
        ) -> torch.FloatTensor:
        """
        function description: Samples z from a multivariate Gaussian with diagonal covariance matrix
        using the reparameterization trick.
        """
        std = var.sqrt()
        eps = torch.rand_like(std)
        z = mu + eps * std # latent code
        return z
	
    def forward(self, x: torch.FloatTensor) -> (
            torch.FloatTensor,
            torch.FloatTensor,
            torch.FloatTensor,
            torch.FloatTensor,
            torch.FloatTensor,
            torch.FloatTensor
        ):
        """
        - data shapes -
        data:
                x --> (batch_size, protein_len, aa_labels)
                mu,var --> (batch_size, z_dim), (batch_size, z_dim)
		z --> (batch_size, z_dim)
		z_upscale --> (batch_size, 1, protein_len)
		logits_xrc --> (batch_size, protein_len, aa_labels)
		y_pred --> (batch_size, 1)
        """
        # q(mu, var|x)
        z_mu, z_var = self.inference(x.permute(0, 2, 1))
        # q(z|x)
        z = self.reparam_trick(z_mu, z_var)
        # upscale latent code
        z_upscale = self.cond_mapper(z)
        # p(x|z)
        logits_xrc = self.generator(x.permute(0,2, 1), z_upscale).permute(0,2,1)
        # p(y|z)
        y_pred_R, y_pred_C = self.discriminator(z)

        return (
                logits_xrc,
                y_pred_R,
                y_pred_C,
                z, 
                z_mu,
                z_var
        )

    @staticmethod
    def compute_kernel(
            x: torch.FloatTensor,
            y: torch.FloatTensor
        ) -> torch.FloatTensor:

        # size of the mini batches
        x_size, y_size = x.shape[0], y.shape[0]

        # dimension based on z size
        dim = x.shape[1] # can also be considered as a hyperparameter

        x = x.view(x_size, 1, dim)
        y = y.view(1, y_size, dim)

        x_core = x.expand(x_size, y_size, dim)
        y_core = y.expand(x_size, y_size, dim)

        return torch.exp(-(x_core-y_core).pow(2).mean(2)/dim)

    @staticmethod
    def compute_mmd(
            x: torch.FloatTensor,
            y: torch.FloatTensor
        ) -> torch.FloatTensor:
        """
        function description: compute the max-mean discrepancy
        arg:
            x --> random distribution z~p(x)
            y --> embedding distribution z'~q(z)
        return:
            MMD_loss --> max-mean discrepancy loss between the sampled noise
                  and embedded distribution
        """

        x_kernel = SS_InfoVAE.compute_kernel(x,x)
        y_kernel = SS_InfoVAE.compute_kernel(y,y)
        xy_kernel = SS_InfoVAE.compute_kernel(x,y)
        return x_kernel.mean() + y_kernel.mean() - 2*xy_kernel.mean()
      
    def compute_loss(
            self,
            xr: torch.FloatTensor,
            x: torch.FloatTensor,
            y_pred_R: torch.FloatTensor,
            y_true_R: torch.FloatTensor,
            y_pred_C: torch.FloatTensor,
            y_true_C: torch.FloatTensor,
            z_pred: torch.FloatTensor,
            true_samples: torch.FloatTensor,
            z_mu: torch.FloatTensor,
            z_var: torch.FloatTensor
        ) -> (
                torch.FloatTensor,
                torch.FloatTensor,
                torch.FloatTensor,
                torch.FloatTensor
        ):

        # POSTERIOR KL-DIVERGENCE loss:
        loss_kld = torch.mean(-0.5 * torch.sum(1 + z_var.log() - z_mu ** 2 - z_var, dim = 1), dim = 0)
        # MMD loss: 
        loss_mmd = SS_InfoVAE.compute_mmd(true_samples, z_pred) # mmd (reg.) loss
        # RECONSTRUCTION loss:
        nll = nn.CrossEntropyLoss(reduction = 'none') # reconstruction loss
        x_nums = torch.argmax(x, dim = -1).long() # convert ground truth from one hot to num. rep.
        loss_nll = nll(xr.permute(0, 2, 1), x_nums) # nll for reconstruction
        #loss_nll = torch.sum(loss_nll, dim = -1) # sum nll along protein sequence
        loss_nll = torch.mean(loss_nll, dim = -1) # average nll along protein sequence
        loss_nll = torch.mean(loss_nll) # average over the batch
        # DISCRIMINATION loss:
        
        try:
            # for classification loss
            loss_pheno_BCE = nn.BCELoss()
            loss_pheno_C = loss_pheno_BCE(y_pred_C, y_true_C)

            # for regression loss
            loss_pheno_MSE = nn.MSELoss()
            loss_pheno_R = loss_pheno_MSE(y_pred_R, y_true_R)

            loss_pheno = loss_pheno_R + loss_pheno_C
        
        except RuntimeErrors: # if the whole batch didn't have experimental true labels
            loss_pheno = torch.tensor([0]).to(self.DEVICE)
                   
 
        return (
                loss_nll,
                loss_kld,
                loss_mmd,
                loss_pheno
        )
    @torch.no_grad()
    def aa_sample(
            self,
            X: torch.FloatTensor,
            option: str='categorical'
        ) -> torch.FloatTensor:
        onehot_transformer = torch.eye(21)

        if option=='categorical': # sample from a categorical distribution
            cate = torch.distributions.Categorical(X)
            X = cate.sample()
        
        else: # sample from an argmax distribution
            X = torch.argmax(X, dim = -1)

        return onehot_transformer[X]

    @torch.no_grad()
    def sample(
            self,
            args: any,
            X_context: torch.FloatTensor,
            z: torch.FloatTensor,
            option: str='categorical',
        ) -> torch.FloatTensor:

        # eval model (important, especially with BatchNorms)
        self.eval()

        # misc helper variables/objects
        protein_len = X_context.shape[1] # length of the maximum sequence
        n = X_context.shape[0] # number of sequences to generate

	# init. placeholder tensors
        X_temp = torch.zeros_like(X_context).to(args.DEVICE) # [B, L, 21]
        X_context = torch.zeros_like(X_context).unsqueeze(1).repeat(1,
                                                               protein_len+1,
                                                               1,
                                                               1
        ).to(args.DEVICE) # [B, L+1, L, 21]

        # upscale latent code
        z_context = self.cond_mapper(z) # linear transformation: [B,6] -> [B, L, 21]
        # generate first index (only latent code conditioning)
        X_gen_logits = self.generator(
                                    X_context[:,0,:,:].permute(0,2,1),
                                    z_context
        ).permute(0, 2, 1) # [B, L, 21]
        # insert amino acid label in the first position
        X_temp[:,0,:] = self.aa_sample(X_gen_logits.softmax(dim=-1), option=option)[:,0]
        # first index of the context is the probability prediction with only latent conditional
        X_context[:,0,:,:] = X_gen_logits.softmax(dim = -1)

        for ii in tqdm(range(1, protein_len)):

            # make logit predictions for the remaining positions
            X_gen_logits = self.generator(
                                    X_temp[:,:,:].permute(0,2,1),
                                    z_context
            ).permute(0,2,1)
            # insert amino acid at the next position
            X_temp[:,ii,:] = self.aa_sample(X_gen_logits.softmax(dim=-1))[:,ii]
            # update the next index of the conditional tensor
            X_context[:,ii,:,:] = X_gen_logits.softmax(dim=-1)
            # update the
            X_context[:,ii,:ii,:] = X_temp[:,:ii,:]

        # last index is the final latent-based AR prediction
        X_context[:,-1,:,:] = X_temp
        return X_context


    @torch.no_grad()
    def diversify(
        self,
        args: any,
        X_context: torch.FloatTensor,
        z: torch.FloatTensor,
        L: int=1,
        option: str='categorical'
        ) -> torch.FloatTensor:
       
        # copy context sequence to track the conditioned amino acids
        X_template = X_context.clone()

        # eval mode (important, especially with BatchNorms)
        self.eval()
        
        # misc helper variables/objects
        protein_len = X_context.shape[1] # length of the maximum sequence
        n = X_context.shape[0] # number of sequences to generate
        
	# init. placeholder tensors
        X_temp = torch.zeros_like(X_context).to(args.DEVICE) # [B, L, 21]
        X_context = torch.zeros_like(X_context).unsqueeze(1).repeat(1,
                                                                   protein_len+1,
                                                                   1,
                                                                   1
        ).to(args.DEVICE) # [B, L+1, L, 21]
        
        # insert the conditioned amino acids
        X_temp[:,:L,:] = X_template[:,:L,:]
        X_context[:,:,:L,:] = X_template.unsqueeze(1).repeat(
                                                        1,
                                                        protein_len+1,
                                                        1,
                                                        1
        )[:,:,:L,:]


        # upscale latent code
        z_context = self.cond_mapper(z) # linear transformation: [B,6] -> [B, L, 21]
       
        for ii in tqdm(range(L, protein_len)):
            
            # make logit predictions for the remaining positions 
            X_gen_logits = self.generator(
                                    X_temp[:,:,:].permute(0,2,1),
                                    z_context
            ).permute(0,2,1)
            # insert amino acid at the next position
            X_temp[:,ii,:] = self.aa_sample(X_gen_logits.softmax(dim=-1))[:,ii]
            # update the next index of the conditional tensor
            X_context[:,ii,:,:] = X_gen_logits.softmax(dim=-1)
            # update the 
            X_context[:,ii,:ii,:] = X_temp[:,:ii,:]
         
        # last index is the final latent-based AR prediction
        X_context[:,-1,:,:] = X_temp
        
        return X_context



    def create_uniform_tensor(
            self,
            args: any,
            X: torch.FloatTensor,
            option: str='random'
        ) -> torch.FloatTensor:

        batch_size, seq_length, aa_length = X.shape

        X_temp = torch.ones_like(X)

        if option == 'random':
            

            X_temp[:,:,-1] = X_temp[:,:,-1]*0 # give no prob to the padded tokens
            X_temp[:,:,:-1] = X_temp[:,:,:-1] / (aa_length-1)

        elif option == 'guided':

            X[:,:,-1] = X[:,:,-1]*0 # remove padded tokens
            X_temp = X_temp - X # removev the ground truth labels
            X_temp[:,:,-1] = X_temp[:,:,-1]*0 # give no prob to the padded tokens
            X_temp[:,:,:-1] = X_temp[:,:,:-1] / (aa_length-2)

        return X_temp

    @torch.no_grad()
    def randomly_diversify(
        self,
        args: any,
        X_context: torch.FloatTensor,
        L: int=1,
        option: str='categorical'
        ) -> torch.FloatTensor:


        # copy context sequence to track the conditioned amino acids
        X_template = X_context.clone()

        # eval mode (important, especially with BatchNorms)
        self.eval()

        # misc helper variables/objects
        protein_len = X_context.shape[1] # length of the maximum sequence
        n = X_context.shape[0] # number of sequences to generate

        # init. placeholder tensors
        X_temp = torch.zeros_like(X_context).to(args.DEVICE) # [B, L, 21]
        X_context = torch.zeros_like(X_context).unsqueeze(1).repeat(1,
                                                                       protein_len+1,
                                                                       1,
                                                                       1
        ).to(args.DEVICE) # [B, L+1, L, 21]

        # insert the conditioned amino acids
        X_temp[:,:L,:] = X_template[:,:L,:]
        X_context[:,:,:L,:] = X_template.unsqueeze(1).repeat(
                                                            1,
                                                            protein_len+1,
                                                            1,
                                                            1
        )[:,:,:L,:]


        for ii in tqdm(range(L, protein_len)):


            # make logit predictions for the remaining positions
            X_logits = self.create_uniform_tensor(
                        args=args,
                        X=X_template
            )

            # insert amino acid at the next position
            X_temp[:,ii,:] = self.aa_sample(X_logits.softmax(dim=-1))[:,ii]
            # update the next index of the conditional tensor
            X_context[:,ii,:,:] = X_logits.softmax(dim=-1)
            # update the context
            X_context[:,ii,:ii,:] = X_temp[:,:ii,:]

            # last index is the final latent-based AR prediction
            X_context[:,-1,:,:] = X_temp

        return X_context


    def pick_pos2mut(self, list_pos: list) -> (
            list,
            int
        ):

        position = np.random.choice((list_pos))
        list_pos.remove(position)

        return (
                list_pos,
                position
        )

    @torch.no_grad()
    def guided_randomly_diversify(
            self,
            args: any,
            X_context: torch.FloatTensor,
            X_design: torch.FloatTensor,
            L: int=1,
            min_leven_dists: list=[],
            option: str='categorical',
            design_seq_lens: list=[],
            ref_seq_len: int=100,
            num_gaps: int=0
        ) -> torch.FloatTensor:

        # copy context sequence to track the conditioned amino acids
        X_template = X_context.clone()

        # eval mode (important, especially with BatchNorms)
        self.eval()

        # misc helper variables/objects
        protein_len = X_context.shape[1] # length of the max seq
        n = X_context.shape[0] # number of sequences to generate

        # init. placeholder tensors
        X_temp = torch.zeros_like(X_context).to(args.DEVICE) # [B, L, 21]
      
        # insert the whole instead of only the conditional info
        X_temp[:,:,:] = X_template[:,:,:]
        X_context = X_template.unsqueeze(1).repeat(
                1,
                protein_len+1,
                1,
                1
        )[:,:,:,:]
        

        # number of sites that fit along the length of the reference sequence
        ref_window_size = (ref_seq_len - L)

        for ii, min_leven_dist in enumerate(min_leven_dists): # how many times to mutate positions
            
            # positions that are allowed to be mutated
            list_pos = [ii for ii in range(L, ref_seq_len)] # get mutating positions
            
            diff = 0 # no need to replace gaps with amino acids
           
            if int(min_leven_dist) > int(ref_window_size):
                
                diff = int(min_leven_dist) - len(list_pos)
                # create new list position to account for longer sequence
                list_pos = [ii for ii in range(L, ref_seq_len + diff)]

            for jj in range(int(min_leven_dist)):

                list_pos, pos_idx = self.pick_pos2mut(list_pos=list_pos)
                # make logit predictions for the remaining positions
                X_logits = self.create_uniform_tensor(
                                    args=args,
                                    X=X_template,
                                    option='guided'
                )

                # insert amino acid at the next position
                X_temp[ii,pos_idx,:] = self.aa_sample(X_logits)[ii,pos_idx]
                # update the next index of the conditional tensor
                X_context[ii,jj,pos_idx,:] = X_logits[ii,pos_idx]
                # last index is the final sample
                X_context[ii,-1,pos_idx,:] = X_temp[ii,pos_idx,:]
          
            # fill in gaps
            X_context[ii,-1,-(num_gaps-diff):,:-1] = 0
            X_context[ii,-1,-(num_gaps-diff):, -1] = 1
                

        print(f'Length start {L} and list positions:', list_pos)
        return X_context

