# %%
import networkx as nx
import numpy as np
import pandas as pd
import scipy.linalg
from scipy.sparse.linalg import eigsh
from scipy.sparse import issparse
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
import matplotlib.cm as cm
# import matplotlib.colors as mcolors
import math
import random
import copy
import time
from mpi4py import MPI
import pickle as pkl
import os
# import json
# import h5py
from tqdm import tqdm
import argparse
import sys
import csv
from scipy.stats import percentileofscore
from statsmodels.stats.multitest import multipletests

if not "SLURM_JOB_ID" in os.environ:
    import pymnet as pn

# Check if the script is running in an environment with predefined sys.argv (like Jupyter or certain HPC environments)
if 'ipykernel_launcher.py' in sys.argv[0] or 'mpirun' in sys.argv[0]:
    # Create a list to hold the arguments you want to parse
    args_to_parse = []

    # Iterate through the system arguments
    for arg in sys.argv:
        # Add only your specified arguments to args_to_parse
        if '--koh' in arg or '--kob' in arg or '--cms' in arg:
            args_to_parse.extend(arg.split('='))
else:
    args_to_parse = sys.argv[1:]  # Exclude the script name

# Command Line Arguments
parser = argparse.ArgumentParser(description='Run QJ Sweeper with command-line arguments.')
parser.add_argument('--koh', type=int, default=40, help='Number of hub nodes to knock out')
parser.add_argument('--kob', type=int, default=5, help='Number of bottom nodes to knock out')
parser.add_argument('--red_range', type=str, default='0.00,0.00,1', help='Range of reduction factors to investigate')
parser.add_argument('--cms', type=str, default='cmsALL', choices=['cmsALL', 'cms123'], help='CMS to use')
# parser.add_argument('--mode', type=str, default='disruption', choices=['disruption', 'transition'], help='Type of knockout analysis')
parser.add_argument('--test_net', type=bool, default=False, help='Boolean for using test nets of various sizes')
parser.add_argument('--pathway', type=bool, default=False, help='Boolean for Pathway Knockout')
parser.add_argument('--path_size_range', type=str, default='5,26', help='Range of reduction factors to investigate for permutation')
parser.add_argument('--permu_runs', type=int, default=None, help='Number of runs for permutation random pathway knockout')
parser.add_argument('--visualize', type=bool, default=False, help='Boolean for visualizing the network')
parser.add_argument('--enrich_file', type=str, default='/home/mbarylli/thesis_code/Diffusion/data_for_diffusion/Pathway_Enrichment_Info_LinkedOmics.csv', help='Path to pathway enrichment file')

args = parser.parse_args(args_to_parse)


# MPI setup
comm = MPI.COMM_WORLD
rank = comm.Get_rank()
size = comm.Get_size()
if "SLURM_JOB_ID" not in os.environ:
    rank = 0
    size = 1

# %%
# Adjust Laplacian Matrix Calculation for Weighted Graph
def weighted_laplacian_matrix(G):
    """
    Calculate the Laplacian matrix for a weighted graph.
    """
    node_order = list(G.nodes())
    # Weighted adjacency matrix
    W = nx.to_numpy_array(G, nodelist=node_order, weight='weight')
    # Diagonal matrix of vertex strengths
    D = np.diag(W.sum(axis=1))
    # Weighted Laplacian matrix
    L = D - W

    return L

def force_dense_eig_solver(L):
    if issparse(L):
        L = L.toarray()  # Convert sparse matrix to dense
    eigenvalues, eigenvectors = np.linalg.eigh(L)  # Use dense method

    return eigenvalues, eigenvectors

def laplacian_exponential_kernel_eigendecomp(L, t):
    """
    Function to compute the Laplacian exponential diffusion kernel using eigen-decomposition
    The function takes the Laplacian matrix L and a time parameter t as inputs.
    """
    # Calculate the eigenvalues and eigenvectors of the Laplacian matrix
    eigenvalues, eigenvectors = force_dense_eig_solver(L)
    # Compute the matrix exponential using eigenvalues
    exp_eigenvalues = np.exp(-t * eigenvalues)
    # Reconstruct the matrix using the eigenvectors and the exponentiated eigenvalues
    kernel = eigenvectors @ np.diag(exp_eigenvalues) @ eigenvectors.T

    return kernel

def laplacian_exponential_diffusion_kernel(L, t):
    """
    compute the Laplacian exponential kernel for a given t value"""
    
    return scipy.linalg.expm(-t * L)

def knockout_node(G, node_to_isolate):
    """
    Isolates a node in the graph by removing all edges connected to it.
    
    :param G: NetworkX graph
    :param node_to_isolate: Node to isolate
    :return: None
    """
    modified_graph = G.copy()
    # Remove all edges to and from this node
    edges_to_remove = list(G.edges(node_to_isolate))
    modified_graph.remove_edges_from(edges_to_remove)
    new_laplacian = weighted_laplacian_matrix(modified_graph)

    return modified_graph, new_laplacian

def knockdown_node_both_layers(G, node_to_isolate_base, reduced_weight=0.3):
    """
    Reduces the weights of all edges connected to a node in both layers of the graph.

    :param G: NetworkX graph
    :param node_to_isolate_base: Base node name whose edges will be reduced in both layers
    :param node_to_isolate_base: Base node name whose edges will be reduced in both layers
    :param reduced_weight: Factor to reduce edge weights by, defaults to 0.5
    :return: Tuple containing the modified graph and its weighted Laplacian matrix
    """

    modified_graph = G.copy()
    
    # Add layer suffixes to the base node name
    node_to_isolate_proteomics = f"{node_to_isolate_base}.p"
    node_to_isolate_transcriptomics = f"{node_to_isolate_base}.t"
    
    # Reduce the weight of all edges to and from this node in both layers
    for node_to_isolate in [node_to_isolate_proteomics, node_to_isolate_transcriptomics]:
        for neighbor in G[node_to_isolate]:
            modified_graph[node_to_isolate][neighbor]['weight'] = reduced_weight
            modified_graph[neighbor][node_to_isolate]['weight'] = reduced_weight
    
    # Compute the weighted Laplacian matrix for the modified graph
    new_laplacian = weighted_laplacian_matrix(modified_graph)

    return modified_graph, new_laplacian, 1, 1, 1

def knockdown_pathway_nodes(G, pathway_description, reduced_weight=0.3):
    """
    Reduces the weights of all edges connected to the nodes in a pathway in both layers of the graph.

    :param G: NetworkX graph
    :param pathway_description: Description of the pathway whose nodes will be reduced in both layers
    :param reduced_weight: Factor to reduce edge weights by, defaults to 0.3
    :return: Tuple containing the modified graph and its weighted Laplacian matrix
    """

    # Find rows where 'description' column contains the given string
    rows = pathway_df[pathway_df['description'] == pathway_description]
    
    # Initialize a list to store the base node names
    base_node_names = []

    # Iterate over the found rows
    for _, row in rows.iterrows():
        # Split the 'genes' column into individual genes and add them to the list
        base_node_names.extend(row['genes'].split('|'))

    modified_graph = G.copy()
    
    # Iterate over the base node names
    for node_to_isolate_base in base_node_names:
        # Add layer suffixes to the base node name
        node_to_isolate_proteomics = f"{node_to_isolate_base}.p"
        node_to_isolate_transcriptomics = f"{node_to_isolate_base}.t"
        
        # Reduce the weight of all edges to and from this node in both layers
        for node_to_isolate in [node_to_isolate_proteomics, node_to_isolate_transcriptomics]:
            if node_to_isolate in G:
                for neighbor in G[node_to_isolate]:
                    modified_graph[node_to_isolate][neighbor]['weight'] = reduced_weight
                    modified_graph[neighbor][node_to_isolate]['weight'] = reduced_weight
    
    # Compute the weighted Laplacian matrix for the modified graph
    new_laplacian = weighted_laplacian_matrix(modified_graph)

    ### COMNNECTED COMPONENTS
    pathway_nodes_with_suffixes = [f"{node}.p" for node in base_node_names] + [f"{node}.t" for node in base_node_names]
    subgraph = modified_graph.subgraph(pathway_nodes_with_suffixes)
    connected_components = list(nx.connected_components(subgraph))
    # get lengths of connected components
    connected_components_lengths = [len(i) for i in connected_components]
    num_disconnected_components = len(connected_components)
    ###


    return modified_graph, new_laplacian, num_disconnected_components, connected_components_lengths, len(pathway_nodes_with_suffixes)


def knockdown_random_nodes(G, node_list, reduced_weight=0.05):
    """
    Reduces the weights of all edges connected to the nodes in a pathway or a list of nodes in both layers of the graph.

    :param G: NetworkX graph
    :param num_nodes: Number of nodes to randomly select for knockdown
    :param reduced_weight: Factor to reduce edge weights by, defaults to 0.3
    :return: Tuple containing the modified graph and its weighted Laplacian matrix
    """


    modified_graph = G.copy()
    
    # Reduce the weight of all edges to and from the random nodes in both layers
    for node in node_list:
        if node in G:  # Check if the node is present in the graph
            for neighbor in G[node]:  # Iterate through its neighbors
                # Reducing the weights
                modified_graph[node][neighbor]['weight'] = reduced_weight
                modified_graph[neighbor][node]['weight'] = reduced_weight
        else:
            print(f"Node {node} not found in graph!!!!!!!!!!!")

    # Form the subgraph for the randomly selected nodes
    subgraph = modified_graph.subgraph(node_list)
    # Find the connected components in the subgraph
    connected_components = list(nx.connected_components(subgraph))
    connected_components_lengths = [len(i) for i in connected_components]

    # Calculate the number of disconnected components (which is the length of connected components list)
    num_disconnected_components = len(connected_components)
    # Compute the weighted Laplacian matrix for the modified graph
    new_laplacian = nx.laplacian_matrix(modified_graph)

    # Return the modified graph, new laplacian, and number of disconnected components
    return modified_graph, new_laplacian, num_disconnected_components, connected_components_lengths, len(node_list)

# %%                  ############################################# double5 DEMO NET#########################

def double5_demonet(inter_layer_weight=1.0):
    # Create a new graph
    G = nx.Graph()
    # Define nodes
    nodes_A = [f"{i}.p" for i in range(1, 6)]
    nodes_B = [f"{i}.t" for i in range(1, 6)]
    # Add nodes
    G.add_nodes_from(nodes_A)
    G.add_nodes_from(nodes_B)
    # Add edges to form a complete circle in each layer
    for i in range(5):
        G.add_edge(nodes_A[i], nodes_A[(i-1)%5])  # A layer
        G.add_edge(nodes_B[i], nodes_B[(i-1)%5])  # B layer
    # Add edges between corresponding nodes of different layers
    for i in range(5):
        G.add_edge(nodes_A[i], nodes_B[i], layer='interlayer')
    # Add manual edges
    G.add_edge("1.t", "3.t")
    G.add_edge("1.p", "4.p")

    weighted_G = G.copy()
    for u, v, data in weighted_G.edges(data=True):
        if data.get('layer') == 'interlayer':
            weighted_G[u][v]['weight'] = inter_layer_weight
        else:
            weighted_G[u][v]['weight'] = 1.0


    # get pandas adjacency
    adj = nx.to_pandas_adjacency(weighted_G)

    lap = weighted_laplacian_matrix(weighted_G)
    # roudn values of lap to 3 decimal places
    lap = np.round(lap, 1)

    return weighted_G, adj, lap

def create_multiplex_test(num_nodes, inter_layer_weight=1.0):
    """
    Creates a multiplex graph with two layers, each having the specified number of nodes.
    """
    G = nx.Graph()

    # Define nodes for each layer
    nodes_layer_p = [f"{i}.p" for i in range(num_nodes)]
    nodes_layer_t = [f"{i}.t" for i in range(num_nodes)]

    # Add nodes
    G.add_nodes_from(nodes_layer_p)
    G.add_nodes_from(nodes_layer_t)

    # Add random edges within each layer
    for _ in range(num_nodes * 2):  # Randomly adding double the number of nodes as edges in each layer
        u, v = np.random.choice(nodes_layer_p, 2, replace=False)
        G.add_edge(u, v, weight=1.0)

        u, v = np.random.choice(nodes_layer_t, 2, replace=False)
        G.add_edge(u, v, weight=1.0)

    # Add edges between corresponding nodes of different layers
    for i in range(num_nodes):
        G.add_edge(nodes_layer_p[i], nodes_layer_t[i], weight=inter_layer_weight)

    return G


###############################################################################
# %% OMICS GRAPH
def weighted_multi_omics_graph(cms, plot=False):
    p = 154 # 136_kpa_lowenddensity or something
    cms = cms

    if "SLURM_JOB_ID" in os.environ:
        adj_matrix_proteomics = pd.read_csv(f'/home/mbarylli/thesis_code/Diffusion/data_for_diffusion/inferred_adjacencies/proteomics_{cms}_adj_matrix_p{p}.csv', index_col=0)
        adj_matrix_transcriptomics = pd.read_csv(f'/home/mbarylli/thesis_code/Diffusion/data_for_diffusion/inferred_adjacencies/transcriptomics_{cms}_adj_matrix_p{p}.csv', index_col=0)
    else: 
        adj_matrix_proteomics = pd.read_csv(f'/home/celeroid/Documents/CLS_MSc/Thesis/EcoCancer/MONIKA/Networks/net_results/inferred_adjacencies/proteomics_{cms}_adj_matrix_p{p}.csv', index_col=0)
        adj_matrix_transcriptomics = pd.read_csv(f'/home/celeroid/Documents/CLS_MSc/Thesis/EcoCancer/MONIKA/Networks/net_results/inferred_adjacencies/transcriptomics_{cms}_adj_matrix_p{p}.csv', index_col=0)


    # Create separate graphs for each adjacency matrix
    G_proteomics_layer = nx.from_pandas_adjacency(adj_matrix_proteomics)
    G_transcriptomic_layer = nx.from_pandas_adjacency(adj_matrix_transcriptomics)

    # # get orphans using function
    # orphans_proteomics = get_orphans(G_proteomics_layer)
    # orphans_transcriptomics = get_orphans(G_transcriptomic_layer)

    # print(f'orphans in proteomics: {orphans_proteomics}')
    # print(f'orphans in transcriptomics: {orphans_transcriptomics}')

    if plot:
        # Calculate the degrees of each node
        degrees = [degree for node, degree in G_proteomics_layer.degree()]
        # Sort the degrees
        degrees_sorted = sorted(degrees, reverse=True)
        # Create an array representing the index of each degree for x-axis
        x = range(len(degrees_sorted))
        # Plotting the line chart
        plt.plot(x, degrees_sorted, label='Transcriptomics Degrees')
        # Adding labels and legend
        plt.legend()
        plt.xlabel('Index')
        plt.ylabel('Degree')
        plt.title('Ordered Degrees of Each Node')
        plt.show()

        # make a histogram of the degrees
        plt.hist(degrees, bins=20)
        plt.xlabel('Degree')
        plt.ylabel('Frequency')
        plt.title('Degree Distribution')
        plt.show()


    # # OR TRY ON A RANDOM ER GRAPH
    # G_proteomics_layer = nx.erdos_renyi_graph(100, 0.1)

    # Function to add a suffix to node names based on layer
    def add_layer_suffix(graph, suffix):
        return nx.relabel_nodes(graph, {node: f"{node}{suffix}" for node in graph.nodes})

    # Create separate graphs for each adjacency matrix and add layer suffix
    G_proteomics_layer = add_layer_suffix(nx.from_pandas_adjacency(adj_matrix_proteomics), '.p')
    G_transcriptomic_layer = add_layer_suffix(nx.from_pandas_adjacency(adj_matrix_transcriptomics), '.t')

    # Create a multiplex graph
    G_multiplex = nx.Graph()

    # Add nodes and edges from both layers
    G_multiplex.add_nodes_from(G_proteomics_layer.nodes(data=True), layer='PROTEIN')
    G_multiplex.add_edges_from(G_proteomics_layer.edges(data=True), layer='PROTEIN')
    G_multiplex.add_nodes_from(G_transcriptomic_layer.nodes(data=True), layer='RNA')
    G_multiplex.add_edges_from(G_transcriptomic_layer.edges(data=True), layer='RNA')

    common_nodes = set(adj_matrix_proteomics.index).intersection(adj_matrix_transcriptomics.index)

    inter_layer_weight = 1
    # Add nodes and edges from both layers
    G_multiplex.add_nodes_from(G_proteomics_layer.nodes(data=True), layer='PROTEIN')
    G_multiplex.add_edges_from(G_proteomics_layer.edges(data=True), layer='PROTEIN')
    G_multiplex.add_nodes_from(G_transcriptomic_layer.nodes(data=True), layer='RNA')
    G_multiplex.add_edges_from(G_transcriptomic_layer.edges(data=True), layer='RNA')

    common_nodes = set(adj_matrix_proteomics.index).intersection(adj_matrix_transcriptomics.index)

    inter_layer_weight = 1
    # Add inter-layer edges for common nodes
    for node in common_nodes:
        G_multiplex.add_edge(f"{node}.p", f"{node}.t",layer='interlayer')

    weighted_G_multiplex = G_multiplex.copy()
    for u, v, data in weighted_G_multiplex.edges(data=True):
        if data.get('layer') == 'interlayer':
            weighted_G_multiplex[u][v]['weight'] = inter_layer_weight
        else:
            weighted_G_multiplex[u][v]['weight'] = 1.0
    for u, v, data in weighted_G_multiplex.edges(data=True):
        if data.get('layer') == 'interlayer':
            weighted_G_multiplex[u][v]['weight'] = inter_layer_weight
        else:
            weighted_G_multiplex[u][v]['weight'] = 1.0


    # PYMNET BS
    if not "SLURM_JOB_ID" in os.environ:
        # Initialize a pymnet multilayer network
        M = pn.MultiplexNetwork(couplings=('categorical', 1), fullyInterconnected=False)

        def preprocess_node_name(node_name):
            # If node ends with '.p' or '.t', remove the suffix
            if node_name.endswith('.p') or node_name.endswith('.t'):
                return node_name[:-2]  # Assuming suffixes are always two characters
            return node_name

        # Add nodes and edges for proteomics layer
        for node in G_proteomics_layer.nodes:
            # Preprocess node names to remove suffixes
            processed_node = preprocess_node_name(node)
            M.add_node(processed_node, layer='PROTEIN')
        for u, v in G_proteomics_layer.edges:
            # Preprocess node names for each edge
            processed_u = preprocess_node_name(u)
            processed_v = preprocess_node_name(v)
            M[processed_u, processed_v, 'PROTEIN', 'PROTEIN'] = 1

        # Add nodes and edges for transcriptomic layer
        for node in G_transcriptomic_layer.nodes:
            # Preprocess node names to remove suffixes
            processed_node = preprocess_node_name(node)
            M.add_node(processed_node, layer='RNA')
        for u, v in G_transcriptomic_layer.edges:
            # Preprocess node names for each edge
            processed_u = preprocess_node_name(u)
            processed_v = preprocess_node_name(v)
            M[processed_u, processed_v, 'RNA', 'RNA'] = 1

        return weighted_G_multiplex, M  #, rna_node_positions
    else:
        return weighted_G_multiplex, None       

# get number of orphans
def get_orphans(G):
    orphans = []
    for node in G.nodes():
        if G.degree(node) == 0:
            orphans.append(node)
    return orphans


# # Display some basic information about the multiplex graph
# if rank == 0:
#     num_nodes = G_multiplex.number_of_nodes()
#     num_edges = G_multiplex.number_of_edges()
#     num_nodes, num_edges

weighted_G_cms_123, pymnet_123 = weighted_multi_omics_graph('cms123', plot=False)
weighted_G_cms_ALL, pymnet_ALL = weighted_multi_omics_graph('cmsALL', plot=False)


orphans_123 = get_orphans(weighted_G_cms_123)
orphans_ALL = get_orphans(weighted_G_cms_ALL)

if orphans_123 or orphans_ALL:
    print(f'Multiplex orphans in cms123: {orphans_123}')  
    print(f'Multiplex orphans in cmsALL: {orphans_ALL}')



# CHOOSING GRAPH #############################################################
##############################################################################
# %%
################################################# ACTIVATE DOUBLE5 DEMO NET #########################################
# weighted_graph_use = double5_net




################################################# NODE AND DIFFUSION PARAMETERS  #########################################
# get hubs and low nodes
degree_dict = dict(weighted_G_cms_ALL.degree(weighted_G_cms_ALL.nodes()))
# get nodes with largest degree and smallest degree
hub_nodes = sorted(degree_dict, key=lambda x: degree_dict[x], reverse=True)[:args.koh]
low_nodes = sorted(degree_dict, key=lambda x: degree_dict[x])[:args.kob]

if rank == 0 and not args.permu_runs:
    print(f'hub nodes: {hub_nodes}')
    print(f'anti-hubs nodes: {low_nodes}')

t_values = np.linspace(0.01, 10, 250)

red_range = args.red_range.split(',')
red_range = np.linspace(float(red_range[0]), float(red_range[1]), int(float(red_range[2])))


if args.koh == 0:
    nodes_to_investigate_bases = [node.split('.')[0] for node in weighted_G_cms_ALL.nodes()] # FOR FIXED REDUCTION, NODE COMPARISON
else:
    nodes_to_investigate_bases = [node.split('.')[0] for node in hub_nodes + low_nodes] # FOR FIXED REDUCTION, NODE COMPARISON

# %%                                                ### RUN FUNCTION DEFINITIONS & MPI PARALLELIZATION ###
# Function to distribute nodes across ranks
def distribute_nodes(nodes, rank, size):
    num_nodes = len(nodes)
    nodes_per_proc = num_nodes // size
    remainder = num_nodes % size

    if rank < remainder:
        start_index = rank * (nodes_per_proc + 1)
        end_index = start_index + nodes_per_proc + 1
    else:
        start_index = remainder * (nodes_per_proc + 1) + (rank - remainder) * nodes_per_proc
        end_index = start_index + nodes_per_proc

    return nodes[start_index:end_index]


# Function to distribute pathways across ranks
def distribute_pathways(pathways, rank, size):
    num_pathways = len(pathways)
    pathways_per_proc = num_pathways // size
    remainder = num_pathways % size

    if rank < remainder:
        start_index = rank * (pathways_per_proc + 1)
        end_index = start_index + pathways_per_proc + 1
    else:
        start_index = remainder * (pathways_per_proc + 1) + (rank - remainder) * pathways_per_proc
        end_index = start_index + pathways_per_proc

    return pathways[start_index:end_index]

def distribute_runs(total_runs, rank, size):
    runs_per_rank = total_runs // size
    start_run = rank * runs_per_rank
    end_run = start_run + runs_per_rank if rank != size - 1 else total_runs  # Ensure the last rank takes any remaining runs
    return range(start_run, end_run)


def generate_node_combinations(G, num_nodes, total_combinations=10000):
    local_random = random.Random(42)  # using a local instance of Random

    # Separate nodes by layer
    proteomics_nodes = [node for node in G.nodes() if node.endswith('.p')]

    unique_combinations = set()
    while len(unique_combinations) < total_combinations:
        # generate a new combination
        # Randomly select half of the nodes from each layer
        random_proteomics_nodes = local_random.sample(proteomics_nodes, num_nodes)
        random_transcriptomics_nodes = [node.replace('.p', '.t') for node in random_proteomics_nodes]  # Get corresponding nodes in the other layer
        
        # Combine both sets of nodes and convert them to a tuple (immutable)
        random_nodes_tuple = tuple(random_proteomics_nodes + random_transcriptomics_nodes)
        unique_combinations.add(random_nodes_tuple)

    return list(unique_combinations)


def run_knockout_analysis(G_aggro, 
                          G_stable, 
                          knockout_type, 
                          knockout_target, 
                          red_range, 
                          t_values, 
                          orig_aggro_kernel, 
                          orig_nonmes_kernel, 
                          orig_gdd_values,  
                          pathway_df=None,
                          run_idx=None):
    results = {}

    if knockout_type == 'runtype_node' or knockout_type == 'runtype_pathway':

        if knockout_type == 'runtype_pathway':
            target_list = pathway_df[pathway_df['description'] == knockout_target]['genes'].str.split('|').explode().tolist()
            num_genes = len(target_list)  # Number of genes in the pathway
            print(f"Gene count in {knockout_target}: {num_genes}\n")


        results[knockout_target] = {}
        for reduction in red_range:
            print(f"Processing {knockout_target} Knockdown with reduction factor: {reduction}")
            # Perform the knockout
            knockdown_func = knockdown_node_both_layers if knockout_type == 'runtype_node' else knockdown_pathway_nodes

            knockdown_graph_aggro, knockdown_laplacian_aggro, _, _, _ = knockdown_func(G_aggro, knockout_target, reduced_weight=reduction)
            knockdown_graph_stable, knockdown_laplacian_stable, _, _, _ = knockdown_func(G_stable, knockout_target, reduced_weight=reduction)

            # Calculate diffusion kernels and GDD
            diff_kernel_knock_aggro = [laplacian_exponential_kernel_eigendecomp(knockdown_laplacian_aggro, t) for t in t_values]
            diff_kernel_knock_stable = [laplacian_exponential_kernel_eigendecomp(knockdown_laplacian_stable, t) for t in t_values]

            gdd_values_trans = np.linalg.norm(np.array(diff_kernel_knock_stable) - np.array(diff_kernel_knock_aggro), axis=(1, 2), ord='fro')**2
            gdd_values_disrupt = np.linalg.norm(np.array(orig_aggro_kernel) - np.array(diff_kernel_knock_aggro), axis=(1, 2), ord='fro')**2

            results[knockout_target][reduction] = {
                'gdd_values_trans': gdd_values_trans,
                'gdd_values_disrupt': gdd_values_disrupt,
                'max_gdd_trans': np.max(np.sqrt(gdd_values_trans)),
                'max_gdd_disrupt': np.max(np.sqrt(gdd_values_disrupt))
            }

            if args and args.visualize:
                results[knockout_target][reduction]['vis_kernels'] = [diff_kernel_knock_aggro[i] for i, t in enumerate(t_values) if i % 20 == 0]

    elif knockout_type == 'runtype_random':

        num_rand_nodes = knockout_target  # Number of genes in the pathway

        unique_node_combinations = generate_node_combinations(G_aggro, num_rand_nodes, total_combinations=args.permu_runs)

        for run_index in run_idx:

            node_list = unique_node_combinations[run_index]
            results[f'random_{num_rand_nodes}_run_{run_index}'] = {}

            for reduction in red_range:
                # Perform the knockout
                knockdown_graph_aggro, knockdown_laplacian_aggro,_,_,_ = knockdown_random_nodes(G_aggro, node_list, reduced_weight=reduction)
                knockdown_graph_stable, knockdown_laplacian_stable,_,_,_ = knockdown_random_nodes(G_stable, node_list, reduced_weight=reduction)

                # Calculate diffusion kernels and GDD
                diff_kernel_knock_aggro = [laplacian_exponential_kernel_eigendecomp(knockdown_laplacian_aggro, t) for t in t_values]
                diff_kernel_knock_stable = [laplacian_exponential_kernel_eigendecomp(knockdown_laplacian_stable, t) for t in t_values]

                gdd_values_trans = np.linalg.norm(np.array(diff_kernel_knock_stable) - np.array(diff_kernel_knock_aggro), axis=(1, 2), ord='fro')**2
                gdd_values_disrupt = np.linalg.norm(np.array(orig_aggro_kernel) - np.array(diff_kernel_knock_aggro), axis=(1, 2), ord='fro')**2

                try:
                    results[f'random_{num_rand_nodes}_run_{run_index}'][reduction] = {
                        'max_gdd_trans': np.max(np.sqrt(gdd_values_trans)),
                        'max_gdd_disrupt': np.max(np.sqrt(gdd_values_disrupt))
                    }
                except KeyError as e:
                    print(f"KeyError encountered! Attempted to access results[{f'random_{num_rand_nodes}_run_{run_index}'}][{reduction}]")
                    print(f"The current keys in results are: {list(results.keys())}")
                    print(f"Contents of the problematic key if it exists: {results.get(f'random_{num_rand_nodes}_run_{run_index}', 'Key does not exist!')}")
                    print(f"Value of num_rand_nodes: {num_rand_nodes}, Value of _: {run_index}, Value of reduction: {reduction}")
                    raise e  # Re-raise the exception to halt the script and indicate error


    return results






# %%  PATHWAY STUFF
# # Joy's Pathways
# file_path = '/home/celeroid/Documents/CLS_MSc/Thesis/EcoCancer/MONIKA/data/Joy_enrichment/results data folder attempt 281223/trans/gem/gProfiler_hsapiens_28-12-2023_11-20-54 am.gem.txt'

# joy_path_df = pd.read_csv(file_path, sep='\t')

# # split 'Genes' column into individual genes
# joy_path_df['Genes'] = joy_path_df['Genes'].str.split(',')
# # for each row, get the length of the list in 'Genes' column
# joy_path_df['Gene Count'] = joy_path_df['Genes'].apply(lambda x: len(x))
# # sort by 'Gene Count' column
# joy_path_df = joy_path_df.sort_values(by='Gene Count', ascending=False)

# joy_path_df.head()

# # print number of rows where 'Gene Count' is between 5 and 25
# print(len(joy_path_df[(joy_path_df['Gene Count'] >= 5) & (joy_path_df['Gene Count'] <= 25)]))

# # rename 'Description' column to 'description'
# joy_path_df = joy_path_df.rename(columns={'Description': 'description'})
# # rename 'Gene Count' column to '# genes'
# joy_path_df = joy_path_df.rename(columns={'Gene Count': '# genes'})

# %%
full_crit_paths = ['Angiogenesis', 'Regulation of angiogenesis', 'Positive regulation of angiogenesis', 'Sprouting angiogenesis', 
                      'Regulation of cell migration involved in sprouting angiogenesis', 'TGF-beta signaling pathway', 'TGF-beta receptor signaling'
                      'TGF-beta signaling in thyroid cells for epithelial-mesenchymal transition', 'Wnt signaling pathway and pluripotency',
                      'Signaling by TGFB family members', 'Canonical and non-canonical TGF-B signaling', 'Transforming growth factor beta receptor signaling pathway',
                      'Cellular response to transforming growth factor beta stimulus', 'Regulation of transforming growth factor beta2 production',
                      'Regulation of transforming growth factor beta receptor signaling pathway', 'Negative regulation of transforming growth factor beta receptor signaling pathway']

crit_paths = ['Regulation of angiogenesis', 'Positive regulation of angiogenesis', 
              'Regulation of cell migration involved in sprouting angiogenesis',
              'Signaling by TGFB family members', 'Transforming growth factor beta receptor signaling pathway',
              'Cellular response to transforming growth factor beta stimulus', 'Regulation of transforming growth factor beta2 production',
              'Regulation of transforming growth factor beta receptor signaling pathway']

pathways = crit_paths

if "SLURM_JOB_ID" not in os.environ:
    args.enrich_file = '/home/celeroid/Documents/CLS_MSc/Thesis/EcoCancer/MONIKA/Diffusion/data/Pathway_Enrichment_Info_LinkedOmics.csv'

    args.pathway = True
    args.koh = 0

if args.pathway:
    if args.koh == 0:
        args.koh = 1000000000000
    pathway_df = pd.read_csv(args.enrich_file) # '/home/mbarylli/thesis_code/Diffusion/data_for_diffusion/Pathway_Enrichment_Info.csv'
    pathway_df = pathway_df.drop_duplicates(subset='description', keep='first')

    # Filter dataframe to only contain rows with 'GO Biological Process' or 'Reactome Pathways' in the 'category' column
    pathway_df = pathway_df[pathway_df['category'].isin(['GO Biological Process', 'Reactome Pathways'])]
    
    # # only keep first X
    filtered_pathway_df = pathway_df # filtered_pathway_df.head(args.koh)
    # # Start with critical pathways

    # # check overlap between 'Description' in joy_path_df and 'description' in pathway_df
    # filtered_pathway_df = pathway_df[pathway_df['description'].isin(joy_path_df['Description'])]

    # keep only rows which have a perc overlap above 0.5
    # filtered_pathway_df = filtered_pathway_df[filtered_pathway_df['perc_overlap'] >= 0.3]
    # Filter the dataframe to include only pathways with '# genes' between min_size and max_size
    min_size, max_size = int(args.path_size_range.split(',')[0]), int(args.path_size_range.split(',')[1])

    filtered_pathway_df = filtered_pathway_df[(filtered_pathway_df['# genes'] >= min_size) & (filtered_pathway_df['# genes'] <= max_size)]

    # Add unique pathways from the filtered list until you reach args.koh
    for pathway in filtered_pathway_df['description'].tolist():
        if len(pathways) >= args.koh:
            break
        if pathway not in pathways:
            pathways.append(pathway)



    matches = pathway_df['description'].isin(pathways)
    interest_pathway_df = pathway_df[matches]

    if "SLURM_JOB_ID" in os.environ:
        # Distribute pathways across ranks
        pathways_subset = distribute_pathways(pathways, rank, size)
        
        print(f'pathways for rank {rank}: {pathways_subset}')
    
        
    else: #Otherwise, if run locally
        pathways_subset = pathways

        print(f'pathways for rank {rank}: {pathways_subset}')



# %%
# DISTRIBUTE NODES ACROSS RANKS
if "SLURM_JOB_ID" in os.environ:
    # Distribute nodes across ranks
    nodes_subset = distribute_nodes(nodes_to_investigate_bases, rank, size)
    print(f'nodes for rank {rank}: {nodes_subset}')
else:
    nodes_subset = nodes_to_investigate_bases
    print(f'nodes for rank {rank}: {nodes_subset}')
    rank = 0
    size = 1

# if "SLURM_JOB_ID" not in os.environ:
    # args.test_net = True

# RUN on test net
if args.test_net:
    # Create two multiplex graphs FOR TESTING
    weighted_G_cms_123 = create_multiplex_test(12)
    weighted_G_cms_ALL = create_multiplex_test(12)

    # Example nodes subset
    nodes_subset_with_suffix = list(weighted_G_cms_123.nodes())
    nodes_subset = list(set([node.split('.')[0] for node in nodes_subset_with_suffix]))
    print(f'nodes subset: {nodes_subset}')

    # Initialize containers for results
    orig_aggro_kernel = [laplacian_exponential_kernel_eigendecomp(weighted_laplacian_matrix(weighted_G_cms_ALL), t) for t in t_values]
    orig_non_mesench_kernel = [laplacian_exponential_kernel_eigendecomp(weighted_laplacian_matrix(weighted_G_cms_123), t) for t in t_values]
    orig_gdd_values = np.linalg.norm(np.array(orig_non_mesench_kernel) - np.array(orig_aggro_kernel), axis=(1, 2), ord='fro')**2
else:
    # Initialize containers for results
    orig_aggro_kernel = [laplacian_exponential_kernel_eigendecomp(weighted_laplacian_matrix(weighted_G_cms_ALL), t) for t in t_values]
    orig_non_mesench_kernel = [laplacian_exponential_kernel_eigendecomp(weighted_laplacian_matrix(weighted_G_cms_123), t) for t in t_values]
    orig_gdd_values = np.linalg.norm(np.array(orig_non_mesench_kernel) - np.array(orig_aggro_kernel), axis=(1, 2), ord='fro')**2


# get max orig_gdd_values
max_orig_gdd_values = np.max(np.sqrt(orig_gdd_values))























# %% 
####################################### RUN # # # # # # # 
start_time = time.time()

local_target_results = {}

if args.pathway:
    # PATHWAY KNOCKOUTS
    for pathway in pathways_subset:
        pathway_results = run_knockout_analysis(weighted_G_cms_ALL, weighted_G_cms_123, 'runtype_pathway', pathway, red_range, t_values, orig_aggro_kernel, orig_non_mesench_kernel, orig_gdd_values, pathway_df)
        local_target_results.update(pathway_results)

    if args.permu_runs:
        # RANDOM PATHWAY KNOCKOUTS (for permutation analysis)
        local_rand_results = {}
        
        # Determine the subset of run indices for each rank
        run_indices = distribute_runs(args.permu_runs, rank, size)

        pathway_sizes = args.path_size_range.split(',')
        pathway_sizes = range(int(pathway_sizes[0]), int(pathway_sizes[1]))
        
        # print(f'pathway sizes for rank {rank}: {pathway_sizes}')
        print(f'run indices for rank {rank}: {run_indices}')

        # Create a boolean series where each element is True if the 'description' column contains any of the pathway descriptions
        for random_pathway_size in pathway_sizes:
            rand_results = run_knockout_analysis(weighted_G_cms_ALL, weighted_G_cms_123, 'runtype_random', random_pathway_size, red_range, t_values, orig_aggro_kernel, orig_non_mesench_kernel, orig_gdd_values, pathway_df, run_idx=run_indices)
            
            local_rand_results.update(rand_results)
            
        print(f'Rank {rank} has finished the target and random pathway runs.')

else:
    for node in nodes_subset:
        # TESTING the knockout analysis function
        node_results = run_knockout_analysis(weighted_G_cms_ALL, weighted_G_cms_123, 'runtype_node', node, red_range, t_values, orig_aggro_kernel, orig_non_mesench_kernel, orig_gdd_values)
        local_target_results.update(node_results)


# GATHERING RESULTS
all_target_results = comm.gather(local_target_results, root=0)
if args.pathway:
    if args.permu_runs:
        all_rand_results = comm.gather(local_rand_results, root=0)
        all_results_list = [all_target_results, all_rand_results]
        filename_identifiers = ['target', 'random']
    else:
        all_results_list = [all_target_results]
        filename_identifiers = ['target']
    
    # Assuming 'pathways' is a list of strings
    unique_identifier = ''.join([pathway[0] for pathway in pathways])
    unique_identifier = unique_identifier[:20]


else:
    all_results_list = [all_target_results]
    filename_identifiers = ['target']

    unique_identifier = ''.join([node[0] for node in nodes_to_investigate_bases])
    unique_identifier = unique_identifier[:5]

for i, all_results in enumerate(all_results_list):
    # Post-processing on the root processor
    if rank == 0 and "SLURM_JOB_ID" in os.environ:
        # Initialize a master dictionary to combine results
        combined_results = {}

        # Combine the results from each process
        for process_results in all_results:
            for key, value in process_results.items():
                combined_results[key] = value

        with open(f'diff_results/Pathway_{args.pathway}_{filename_identifiers[i]}_{unique_identifier}_GDDs_ks{str(orig_aggro_kernel[0].shape[0])}_permu{args.permu_runs}.pkl', 'wb') as f:
            pkl.dump(combined_results, f)
        
        os.system("cp -r diff_results/ $HOME/thesis_code/Diffusion/")
        print('Saving has finished.')


    elif rank == 0 and "SLURM_JOB_ID" not in os.environ:
        with open(f'diff_results/LOCAL_Pathway_{args.pathway}_{filename_identifiers[i]}_{unique_identifier}_GDDs_ks{str(orig_aggro_kernel[0].shape[0])}_permu{args.permu_runs}.pkl', 'wb') as f:
            pkl.dump(local_target_results, f)

        # with open(f'diff_results/Pathway_{args.pathway}_random_node_{unique_identifier}_GDDs_ks{str(orig_aggro_kernel[0].shape[0])}.pkl', 'wb') as f:
        #     pkl.dump(local_rand_results, f)


# get the end time
end_time = time.time()
print(f'elapsed time (node knockdown calc) (rank {rank}): {end_time - start_time}')



MPI.Finalize()
























# %% SIGNIFICANCE TESTING

if "SLURM_JOB_ID" not in os.environ:

    # args.pathway = True
    unique_identifier = 'RPRSTCRR'

    # Load results and pathway information
    with open('diff_results/Pathway_True_target_RPRSTCRRBARCTRmSATDS_GDDs_ks308_permuNone.pkl', 'rb') as f:
        target_results = pkl.load(f)
    with open('diff_results/Pathway_True_random_RPRSTCRR_GDDs_ks308_permu10368.pkl', 'rb') as f:
        random_results = pkl.load(f)

    # print keys of random results
    print(random_results['random_11_run_1'][0.00]['max_gdd_trans'])
    print(random_results.keys())
    # print(len(random_results['random_11_run_1'][0.05]['max_gdd_trans']))

        
    def get_pathway_length(pathway_name, df):
        if pathway_name in df['description'].values:
            row = df[df['description'] == pathway_name].iloc[0]
            return len(row['genes'].split('|'))
        else:
            # Handle the case where pathway_name is not found in df
            # For example, return a default value or raise a custom error
            return None  # or raise ValueError(f"Pathway '{pathway_name}' not found in dataframe")

    # Function to parse random results keys
    def parse_random_key(key):
        parts = key.split('_')
        return int(parts[1]), int(parts[3])  # length, run_number

    # Organize random results by pathway length
    random_results_by_length = {}  # {pathway_length: [list of max_gdd_trans values]}
    for key, result in random_results.items():
        length, run_number = parse_random_key(key)
        max_gdd_trans = result[0.00]['max_gdd_trans']
        if length not in random_results_by_length:
            random_results_by_length[length] = []
        random_results_by_length[length].append(max_gdd_trans)

    # for key in random_results_by_length.keys():
    #     print(len(random_results_by_length[key]))



    # Calculate p-values
    p_values = {}
    for pathway, result in target_results.items():
        target_max_gdd_trans = result[0.00]['max_gdd_trans']
        pathway_length = get_pathway_length(pathway, interest_pathway_df)
        if pathway_length is not None:
            random_distribution = random_results_by_length.get(pathway_length, [])
            if random_distribution:  # Ensure there are random results for this length
                p_value = percentileofscore(random_distribution, target_max_gdd_trans, kind='mean') / 100
                # if p_value == 0:
                #     p_value = "<0.001"
                p_values[pathway] = p_value
            else:
                # Handle case where there are no random results for this length
                p_values[pathway] = None  # or some other placeholder
        else:
            print(f"Pathway length {pathway_length} not found for {pathway}")
            # Handle case where pathway length could not be determined
            p_values[pathway] = None  # or some other placeholder


    # Filter out None values from p_values
    filtered_p_values = {k: v for k, v in p_values.items() if v is not None}

    # Adjust for multiple testing on the filtered p-values
    adjusted_p_values = multipletests(list(filtered_p_values.values()), method='fdr_bh')[1]
    adjusted_p_values_dict = dict(zip(filtered_p_values.keys(), adjusted_p_values))

    # Merge adjusted p-values back with the original set (assigning None where appropriate)
    final_adjusted_p_values = {pathway: adjusted_p_values_dict.get(pathway, None) for pathway in p_values.keys()}

    # Interpret results
    significant_pathways = {pathway: adj_p for pathway, adj_p in final_adjusted_p_values.items() if adj_p is not None and adj_p <= 0.05}

    # Display significant pathways
    print("Significant Pathways (adjusted p-value ≤ 0.05):")
    for pathway, adj_p in significant_pathways.items():
        print(f"{pathway}: {adj_p}")


    # Create a DataFrame for target results including pathway length
    data = []

    # put max_orig_gdd_values in the first row
    data.append(['ORIGINAL_MAX_GDD', max_orig_gdd_values, None, None])

    for pathway, result in target_results.items():
        max_gdd_trans = result[0.00]['max_gdd_trans']
        p_value = p_values.get(pathway, None)  # Get the p-value, if available
        pathway_length = get_pathway_length(pathway, interest_pathway_df)  # Get pathway length
        data.append([pathway, max_gdd_trans, p_value, pathway_length])

    # Creating the DataFrame with an additional column for pathway length
    target_df = pd.DataFrame(data, columns=['Pathway', 'Max_GDD_Trans', 'P_Value', 'Pathway_Length'])


    # Sorting the DataFrame by Max_GDD_Trans in ascending order
    target_df_sorted = target_df.sort_values(by='P_Value', ascending=True)

    # Display the first few rows of the sorted DataFrame
    print(target_df_sorted.head())

    # write to csv
    permu_runs = 'whatevs'
    target_df_sorted.to_csv(f'diff_results/PATHWAY_KNOCKOUTS_RESULTS_permu_{permu_runs}.csv', index=False)


    
    # Filter for pathways with 0 p-value
    zero_p_value_pathways = [pathway for pathway, p_val in p_values.items() if p_val != 0]
    curated_paths = ['Negative regulation of the PI3K/AKT network', 'Regulation of receptor signaling pathway via JAK-STAT', 
    'Positive regulation of MAPK cascade']

    if True: 
        # Plot distributions
        for pathway in curated_paths:
            # Get target max_gdd_trans value
            target_max_gdd_trans = target_results[pathway][0.00]['max_gdd_trans']

            # Get pathway length
            pathway_length = get_pathway_length(pathway, interest_pathway_df)

            # Get corresponding random distribution
            random_distribution = random_results_by_length.get(pathway_length, [])

            # Plotting
            plt.figure(figsize=(10, 6))
            plt.hist(random_distribution, bins=100, alpha=0.7, label='Random Distribution')
            plt.axvline(x=target_max_gdd_trans, color='r', linestyle='dashed', linewidth=2, label='Target Value')
            plt.title(f"Distribution for Pathway: {pathway} (Length: {pathway_length})")
            plt.xlabel('Max GDD Trans')
            plt.ylabel('Frequency')
            # plt.xlim(0.94, 1.05)
            plt.legend()
            plt.show()

    

    # GDD PLOTTING
    # Extract all target GDD values and sort them
    all_gdd_values = [(pathway, result[0.00]['max_gdd_trans']) for pathway, result in target_results.items()]
    sorted_gdd_values = sorted(all_gdd_values, key=lambda x: x[1])

    # Select top 3 and bottom 3 targets based on GDD values
    top_3_gdd = sorted_gdd_values[-3:]
    bottom_3_gdd = sorted_gdd_values[:3]

    # Plotting section adapted for the new structure
    plt.figure(figsize=(20, 12))

    # Plot for the top 3 GDD values
    for target, gdd_value in top_3_gdd:
        # Assuming the new code stores a list or similar iterable of GDD values
        plt.plot(t_values, target_results[target][0.00]['gdd_values_trans'], label=f'Top {target} GDD')

    # Plot for the bottom 3 GDD values
    for target, gdd_value in bottom_3_gdd:
        plt.plot(t_values, target_results[target][0.00]['gdd_values_trans'], label=f'Bottom {target} GDD')

    plt.title('GDD Over Time (Trans) for Top 3 and Bottom 3 Targets')
    plt.xlabel('Time')
    plt.ylabel('GDD Value')
    plt.legend()
    plt.show()











    # %%  NODE KNOCKOUT CODE
    cms = 'cmsALL'

    with open(f'diff_results/Pathway_False_target_YYETA_GDDs_ks308_permuNone.pkl', 'rb') as f:
        node_knockouts = pkl.load(f)

    print(f'node_knockouts: {node_knockouts.keys()}')
    print(f'Reduction factors: {node_knockouts[list(node_knockouts.keys())[0]].keys()}')
    # print(f'GDD values: {node_knockouts[list(node_knockouts.keys())[0]][0.05]}')

    for key in node_knockouts.keys():
        print(f'node {key}: {node_knockouts[key].keys()}')
    # Choose the node and t_values for plotting
    selected_node = np.random.choice(list(node_knockouts.keys()))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 6), dpi=300)

    # Left plot: GDD values over time for various reductions (single node)
    for reduction in node_knockouts[selected_node].keys():
        gdd_values = node_knockouts[selected_node][reduction]['gdd_values_trans']
        ax1.plot(t_values, gdd_values, label=f'Reduction {reduction}')

    ax1.set_title(f'GDD Over Time for Various Reductions\nNode: {selected_node}')
    ax1.set_xlabel('Time')
    ax1.set_ylabel('GDD Value')
    ax1.legend()
    ax1.grid(True)

    # Choose a reduction factor from the list of reductions
    selected_reduction = red_range[0]
    selected_reduction = 0.00

    max_gdds = {}
    # Right plot: GDD values over time for a single reduction (all nodes)
    for node_base in node_knockouts.keys():
        gdd_values = node_knockouts[node_base][selected_reduction]['gdd_values_trans']
        ax2.plot(t_values, gdd_values, label=f'Node {node_base}', alpha=0.5)
        max_gdds[node_base] = np.max(gdd_values)

    ax2.set_title(f'GDD Over Time for Single Reduction\nReduction: {selected_reduction}')
    ax2.set_xlabel('Time')
    # ax2.set_ylabel('GDD Value')  # Y-label is shared with the left plot
    # ax2.legend()
    ax2.set_xlim([0, 2])
    ax2.grid(True)

    plt.show()
    # print(max_gdds)
    # max_GDD_1 = max_gdds['1']
    # max_GDD_2 = max_gdds['2']
    # print(max_GDD_1 - max_GDD_2)

    # print max_gdds in ascending order
    sorted_max_gdds = {k: v for k, v in sorted(max_gdds.items(), key=lambda item: item[1])}
    # print original max_gdd 
    print(max_orig_gdd_values)
    print(sorted_max_gdds)

    summed_degrees = {}
    for node in sorted_max_gdds.keys():
        node_p = node + '.p'
        node_t = node + '.t'
        
        # Accessing the degree for each node directly from the DegreeView
        degree_p = weighted_G_cms_ALL.degree(node_p, weight='weight') if node_p in weighted_G_cms_ALL else 0
        degree_t = weighted_G_cms_ALL.degree(node_t, weight='weight') if node_t in weighted_G_cms_ALL else 0
        
        summed_degree = degree_p + degree_t
        summed_degrees[node] = summed_degree

    # Print the summed degrees
    print(summed_degrees)

    # Make dataframe with node, max_gdd, and summed_degree
    df = pd.DataFrame.from_dict(sorted_max_gdds, orient='index', columns=['max_gdd'])
    # add row with original max_gdd
    df.loc['ORIGINAL_MAX_GDD'] = max_orig_gdd_values
    # make column with max_gdd delta
    df['max_gdd_delta'] = df['max_gdd'] - df.loc['ORIGINAL_MAX_GDD', 'max_gdd']
    # make column with max_gdd / original_max_gdd
    df['max_gdd_ratio'] = df['max_gdd'] / df.loc['ORIGINAL_MAX_GDD', 'max_gdd']
    df['summed_degree'] = df.index.map(summed_degrees)
    df = df.sort_values(by='max_gdd', ascending=True)

    print(df.head())

    # write to file
    df.to_csv(f'diff_results/NODE_KNOCKOUTS_RESULTS.csv', index=True)

    # %% SAVE FOR LATER
    # PLOT 2 (WEAKER KNOCKDOWN)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 6), dpi=300)


    # make another plot as ax2 but for reduction factor = 0.8
    selected_reduction = red_range[-1]

    max_gdds = {}
    # Right plot: GDD values over time for a single reduction (all nodes)
    for node_base in node_knockouts.keys():
        gdd_values = node_knockouts[node_base][selected_reduction]['gdd_values']
        ax2.plot(t_values, gdd_values, label=f'Node {node_base}', alpha=0.5)
        max_gdds[node_base] = np.max(gdd_values)


    ax2.set_title(f'GDD Over Time for Single Reduction\nReduction: {selected_reduction}')
    ax2.set_xlabel('Time')
    # ax2.set_ylabel('GDD Value')  # Y-label is shared with the left plot
    # ax2.legend()
    ax2.set_xlim([0, 2])
    ax2.grid(True)

    plt.show()
    # print(max_gdds)
    # max_GDD_1 = max_gdds['1']
    # max_GDD_2 = max_gdds['2']
    # print(max_GDD_1 - max_GDD_2)

    selected_reduction = red_range[-1]
    # order nodes by max GDD
    max_gdds = {}
    for node_base in node_knockouts.keys():
        max_gdds[node_base] = np.max(node_knockouts[node_base][selected_reduction]['gdd_values'])

    sorted_max_gdds = {k: v for k, v in sorted(max_gdds.items(), key=lambda item: item[1])}

    # get the nodes with the highest GDD
    highest_gdd_nodes = list(sorted_max_gdds.keys())[-5:]
    highest_gdd_nodes



















# %%
# VISUALIZE DIFFUSION
args.visualize = True

def multiplex_net_viz(M, ax, diff_colors=False, node_colors=None, node_sizes=None, node_coords=None):

    dark_red = "#8B0000"  # Dark red color
    dark_blue = "#00008B"  # Dark blue color

    # Iterate over nodes in the 'PROTEIN' layer
    # Initialize a dictionary to hold node colors
    for node in M.iter_nodes(layer='PROTEIN'):
        if not diff_colors:
            node_colors[(node, 'PROTEIN')] = dark_red

    # Iterate over nodes in the 'RNA' layer
    for node in M.iter_nodes(layer='RNA'):
        if not diff_colors:
            node_colors[(node, 'RNA')] = dark_blue


    edge_color = "#505050"  # A shade of gray, for example

    # Initialize a dictionary to hold edge colors
    edge_colors = {}

    # Assign colors to edges in the 'PROTEIN' and 'RNA' layers
    # Assuming edges are between nodes within the same layer
        
    layer_colors = {'PROTEIN': "red", 'RNA': "blue"}

    fig = pn.draw(net=M,
            ax=ax,
            show=False, 
            nodeColorDict=node_colors,
            nodeLabelDict={nl: None for nl in M.iter_node_layers()},  # Set all node labels to None explicitly
            nodeLabelRule={},  # Clear any label rules
            defaultNodeLabel=None,  # Set default label to None
            nodeSizeDict=node_sizes,
            # nodeSizeRule={"rule":"degree", "scalecoeff":0.00001},
            nodeCoords=node_coords,
            edgeColorDict=edge_colors,
            defaultEdgeAlpha=0.1,
            layerColorDict=layer_colors,
            defaultLayerAlpha=0.075,
            # layerLabelRule={},  # Clear any label rules
            # defaultLayerLabel=None,  # Set default label to None
            # azim=45,
            elev=25)

    print(type(fig))

    return fig

# %%


def multiplex_diff_viz(M, weighted_G, ax=None, node_colors=None, node_sizes=None):
    # Load the pickle file
    with open('diff_results/Pathway_False_target_BB_GDDs_ks272.pkl', 'rb') as f:
        results = pkl.load(f)
    
    # with open('diff_results/Pathway_False_target_BCGGS_GDDs_ks308_permuNone.pkl', 'rb') as f:
    #     results = pkl.load(f)
    
    print(results.keys())
    time_resolved_kernels = results['BIRC2'][0.05]['vis_kernels'][:12] # BAK1


    # Assume 'weighted_G_cms_ALL' is your graph
    node_order = list(weighted_G.nodes())
    # get indices of nodes that end with '.t'
    t_indices = [i for i, node in enumerate(node_order) if node.endswith('.t')]
    #consistent node positions

    nodes_with_degree = [node for node, degree in weighted_G_cms_ALL.degree() if degree > 0 and node.endswith('.p')]

    # Create a subgraph with these nodes
    subgraph = weighted_G_cms_ALL.subgraph(nodes_with_degree)

    # Now calculate the layout using only the nodes in the subgraph
    pos = nx.spring_layout(subgraph)

    print(pos)

    # If you want to use the same positions for the nodes in the original graph, you can do so. 
    # The orphan nodes will be assigned a default position as they are not included in the subgraph.
    prot_node_positions = pos.copy()
    for node in weighted_G_cms_ALL.nodes():
        if node not in prot_node_positions and node.endswith('.p'):
            print(node)
            prot_node_positions[node] = (0, 0)  # or any other default position


    # Set up a 3x3 subplot grid with 3D projection
    fig, axes = plt.subplots(nrows=1, ncols=3, figsize=(20, 10), subplot_kw={'projection': '3d'}, dpi = 600)
    axs = axes.flatten()  # Flatten the axes array for easy iteration
    # fig.suptitle('Network Diffusion over 25 Time Points')

    # Define the suffixes for each layer
    suffixes = {'PROTEIN': '.p', 'RNA': '.t'}

    # Create an index mapping from the suffixed node name to its index
    node_indices = {node: i for i, node in enumerate(node_order)}
    node_colors = {}  
    node_sizes = {} 
    node_coords = {}

    for node, pos in prot_node_positions.items():
        stripped_node = node.rstrip('.t')  # Remove the suffix to match identifiers in M
        node_coords[stripped_node] = pos

    for nl in M.iter_node_layers():  # Iterating over all node-layer combinations
        node, layer = nl  # Split the node-layer tuple
        neighbors = list(M._iter_neighbors_out(nl, dims=None))  # Get all neighbors for the node-layer tuple
        degree = len(neighbors)  # The degree is the number of neighbors
        
        # Assign to node sizes
        node_sizes[nl] = 0.05 # 0.0001 * degree**2  # 0.0015 * degree # Adjust the scaling factor as needed

    print(node_sizes)


    j = 271
    global_max = max(kernel.max() for kernel in time_resolved_kernels)
    norm = Normalize(vmin=0, vmax=1)
    # print(global_max)
    # Ensure you have a list of the 25 time-resolved kernels named 'time_resolved_kernels'
    for idx, (ax, kernel) in enumerate(zip(axs, time_resolved_kernels)):
        # if idx % 5 == 0:
        # Create the unit vector e_j with 1 at the jth index and 0 elsewhere
        e_j = np.zeros(len(weighted_G.nodes()))
        e_j[j] = 100

        # e_j = np.ones(len(weighted_G_cms_ALL.nodes()))
        
        # Multiply the kernel with e_j to simulate diffusion from node j
        diffusion_state = kernel @ e_j
        # order the diffusion state in descending order
        diffusion_state = sorted(diffusion_state, reverse=True)

        # Now, update node colors and sizes based on diffusion_state
        for layer in M.iter_layers():  # Iterates through all nodes in M
            # Determine the layer of the node for color and size settings
            for node in M.iter_nodes(layer=layer):
                # Append the appropriate suffix to the node name to match the format in node_order
                suffixed_node = node + suffixes[layer]
                # Use the suffixed node name to get the corresponding index from the node_order
                index = node_indices[suffixed_node]

                # Map the diffusion state to a color and update node_colors and node_sizes
                color = plt.cm.viridis(diffusion_state[index])  # Mapping color based on diffusion state
                node_colors[(node, layer)] = color
                # node_sizes[(node, layer)] = 0.03  # Or some logic to vary size with diffusion state

        # Now use your updated visualization function with the new colors and sizes
        diff_fig = multiplex_net_viz(M, ax, diff_colors=True, node_colors=node_colors, node_sizes=node_sizes, node_coords=node_coords)

        ax.set_title(f"Time Step {idx*20}")

        # ax.imshow(diff_fig)

        # # Draw the graph with node colors based on the diffusion state
        # nx.draw(weighted_G_cms_ALL, pos, ax=ax, node_size=50,
        #         node_color=norm(diffusion_state), cmap=plt.cm.viridis,
        #         edge_color=(0, 0, 0, 0.5), width=1.0)  # Use a simple color for edges for clarity

        # # # print maximum value of kernel at this time step
        # # print(f'max kernel value at time step {idx}: {kernel.max()}')

        # # Set a title for each subplot indicating the time step
        # ax.set_title(f"Time Step {idx*20}")

    # Adjust layout to prevent overlap
    # plt.tight_layout()
    # plt.subplots_adjust(top=0.95)  # Adjust the top space to fit the main title
    # plt.colorbar(cm.ScalarMappable(norm=norm, cmap=plt.cm.viridis), ax=axs.ravel().tolist(), orientation='vertical')

    # savethefigure
    plt.savefig('diffusion_figure.png', dpi=600) # make rc.param no font export

    print('wot')
    # Display the figure
    plt.show()

if args.visualize:
    multiplex_diff_viz(pymnet_ALL, weighted_G_cms_ALL)


# %% COMPARING DIRECT KERNEL WITH KERNEL EIGENDECOMPOSITION
# # start time
# start_time = time.time()
# # calculate laplacian_exponential_diffusion_kernel for knockdown graph for 5 different t values
# knockdown_kernel = [laplacian_exponential_kernel_eigendecomp(knockdown_laplacian, t) for t in t_values]

# mid_time = time.time()
# eigentimes = mid_time - start_time

# # check whether we get same result for laplacian_exponential_diffusion_kernel
# knockdown_kernel_direct = [laplacian_exponential_diffusion_kernel(knockdown_laplacian, t) for t in t_values]

# # end time
# end_time = time.time()
# directtimes = end_time - mid_time

# np.allclose(knockdown_kernel, knockdown_kernel_direct)

# print(f'Eigen-decomposition time: {eigentimes}')
# print(f'Direct computation time: {directtimes}')

# %%
# from scipy.sparse.linalg import eigsh
# from scipy.sparse import csr_matrix, issparse

# def force_dense_eig_solver(L):
#     if issparse(L):
#         L = L.toarray()  # Convert sparse matrix to dense
#     eigenvalues, eigenvectors = np.linalg.eigh(L)  # Use dense method
#     return eigenvalues, eigenvectors

# def force_sparse_eig_solver(L, k=6):  # You can adjust 'k' as needed
#     # Ensure L is a sparse matrix for eigsh, if not, convert it.
#     if not issparse(L):
#         L = csr_matrix(L)  # Convert dense matrix to sparse format, choose appropriate sparse format like csr or csc
#     eigenvalues, eigenvectors = eigsh(L, k=k)  # Use sparse method
#     return eigenvalues, eigenvectors


# def laplacian_exponential_kernel_eigendecomp(L, t, sparse=False):
#     """
#     Function to compute the Laplacian exponential diffusion kernel using eigen-decomposition
#     The function takes the Laplacian matrix L and a time parameter t as inputs.
#     """
#     # Calculate the eigenvalues and eigenvectors of the Laplacian matrix
#     if sparse:
#         N = L.shape[0]
#         eigenvalues, eigenvectors = force_sparse_eig_solver(L, k=N-1)
#     else:
#         eigenvalues, eigenvectors = force_dense_eig_solver(L)
#     # Compute the matrix exponential using eigenvalues
#     exp_eigenvalues = np.exp(-t * eigenvalues)
#     # Reconstruct the matrix using the eigenvectors and the exponentiated eigenvalues
#     kernel = eigenvectors @ np.diag(exp_eigenvalues) @ eigenvectors.T

#     return kernel


# t_values = np.linspace(0.01, 10, 50)
# reduction = 0.05

# for knockout_target in pathways_subset:

#     t = 0.5

#     start_time = time.time()
#     knockdown_graph_aggro, knockdown_laplacian, _, _, _ = knockdown_pathway_nodes(weighted_G_cms_ALL, knockout_target, reduced_weight=reduction)

#     # Calculate laplacian exponential diffusion kernel for knockdown graph for different t values using eigen-decomposition
#     # knockdown_kernel_eigendecomp = [laplacian_exponential_kernel_eigendecomp(knockdown_laplacian, t, sparse=False) for t in t_values]
#     knockdown_kernel_eigendecomp = laplacian_exponential_kernel_eigendecomp(knockdown_laplacian, t, sparse=False)


#     # Mid-time between the methods
#     mid_time = time.time()

#     # knockdown_sparse_kernel_eigendecomp = [laplacian_exponential_kernel_eigendecomp(knockdown_laplacian, t, sparse=True) for t in t_values]
#     knockdown_sparse_kernel_eigendecomp = laplacian_exponential_kernel_eigendecomp(knockdown_laplacian, t, sparse=True)

#     mid2_time = time.time()

#     # Calculate laplacian exponential diffusion kernel for knockdown graph for different t values using direct method
#     # knockdown_kernel_direct = [laplacian_exponential_diffusion_kernel(knockdown_laplacian, t) for t in t_values]
#     knockdown_kernel_direct = laplacian_exponential_diffusion_kernel(knockdown_laplacian, t)
    

#     # End time for direct method
#     end_time = time.time()

#     eigen_times = mid_time - start_time
#     eigen_times_sparse = mid2_time - mid_time
#     direct_times = end_time - mid2_time

#     # Check if the results from both methods are close to each other
#     are_close = np.allclose(knockdown_kernel_eigendecomp, knockdown_kernel_direct)

#     are_close_sparse = np.allclose(knockdown_sparse_kernel_eigendecomp, knockdown_kernel_eigendecomp, atol=1e-1, rtol=1e-1)

#     if are_close_sparse == False:
#         # Print time taken by each method and the result of the comparison
#         print(f'Eigen-decomposition time: {eigen_times}')
#         print(f'Eigen-decomposition time (sparse): {eigen_times_sparse}')
#         print(f'Direct computation time: {direct_times}\n')
#         print(f'Are the results from direct and eigendecomp methods close? {are_close}')
#         print(f'Are the results from sparse and eigendecomp methods close? {are_close_sparse}')

#         A = knockdown_sparse_kernel_eigendecomp
#         B = knockdown_kernel_eigendecomp

#         C = knockdown_kernel_direct

#         # Compute the Frobenius norm of the difference
#         frobenius_norm = np.linalg.norm(A - B, 'fro')

#         print("The Frobenius norm of the difference is:", frobenius_norm)

#         frob_norm_direct = np.linalg.norm(B - C, 'fro')

#         print("The Frobenius norm of the difference is (direct to eigenfull):", frob_norm_direct)




# %%
### BELOW HERE IS THE CODE GRAVEYARD ##########################################



# # %% ############################ BARBELL GRAPHS ############################
# # Let's generate the three barbell graphs as shown in the image.
# # The first graph will be a complete barbell graph, the second will have its bridge removed, 
# # and the third will have its central connection removed.

# # Define the number of nodes in the barbell graph's complete subgraphs and the bridge length
# m1 = 5  # Number of nodes in the complete subgraphs
# m2 = 0  # Number of nodes in the bridge

# # Generate the complete barbell graph
# G_single_edge = nx.barbell_graph(m1, m2)

# # Identify the nodes to move and disconnect
# node_to_move_from_first_bell = m1 - 2  # Second to last node in the first bell
# node_to_move_from_second_bell = m1 + 1  # Second node in the second bell

# G_complete = G_single_edge.copy()
# # Add the new edge directly connecting the two identified nodes
# G_complete.add_edge(node_to_move_from_first_bell, node_to_move_from_second_bell)

# G_cc = G_complete.copy()
# # remove edge between nodes 7 and 9
# G_cc.remove_edge(7, 9)

# # Verify the graphs by plotting them
# fig, axes = plt.subplots(1, 3, figsize=(18, 6))
# # Plot the complete barbell graph
# nx.draw(G_complete, ax=axes[0], with_labels=True)
# axes[0].set_title('Complete Barbell Graph')
# # Plot the barbell graph with the bridge removed
# nx.draw(G_single_edge, ax=axes[1], with_labels=True)
# axes[1].set_title('Barbell Graph with Bridge Removed')
# # Plot the barbell graph with the bell connection removed
# nx.draw(G_cc, ax=axes[2], with_labels=True)
# axes[2].set_title('Barbell Graph with bell connection removed')

# plt.tight_layout()
# plt.show()



# %% PLOTTING THE MAX GDD
# # Compute the Laplacian Matrices for both graphs
# laplacian_A = weighted_laplacian_matrix(G_complete)
# laplacian_B = weighted_laplacian_matrix(G_cc)

# # Step 3 and 4: Compute the Laplacian Kernels and calculate the xi values over a range of t values
# t_values = np.linspace(0, 10, 1000)
# xi_values = []

# for t in t_values:
#     kernel_A = laplacian_exponential_kernel_eigendecomp(*np.linalg.eigh(laplacian_A), t)
#     kernel_B = laplacian_exponential_kernel_eigendecomp(*np.linalg.eigh(laplacian_B), t)
#     xi = np.linalg.norm((kernel_A - kernel_B), 'fro')
#     xi_values.append(xi)

# # Find the maximum xi value and the corresponding t value using line search
# max_xi = max(xi_values)
# max_xi_index = xi_values.index(max_xi)
# max_xi_time = t_values[max_xi_index]

# # Step 5: Plot xi against t
# plt.figure(figsize=(10, 6))
# plt.plot(t_values, xi_values, label='xi(t)')
# plt.plot(max_xi_time, max_xi, 'ro', label='Max xi')
# # add a text label at maximum point
# plt.annotate(f'Max xi = {round(max_xi, 2)}\n at t = {round(max_xi_time, 2)}', xy=(max_xi_time, max_xi), xytext=(max_xi_time + 1, max_xi - 0.1),
#              )
# plt.xlabel('Diffusion Time (t)')
# plt.ylabel('Xi Value (Graph Difference)')
# plt.title('Xi Values Over Diffusion Time for Two Graphs')
# plt.legend()
# plt.grid(True)
# plt.show()


# %% TESTING EIGENDECOMP
# # TESTING ON SCALE_FREE GRAPH WITH RANDOM WEIGHTS
# weighted_laplacian = weighted_laplacian_matrix(weighted_scale_free_graph)

# # Eigen-decomposition of the Laplacian matrix
# eigenvalues, eigenvectors = np.linalg.eigh(laplacian_matrix)
# # Test the eigen-decomposition approach for a single t value
# t_test = 0.5
# kernel_eigendecomp = laplacian_exponential_kernel_eigendecomp(eigenvalues, eigenvectors, t_test)
# # Compare with the direct computation for verification
# kernel_direct = laplacian_exponential_diffusion_kernel(laplacian_matrix, t_test)
# # Check if the results are similar (within a small numerical tolerance)
# np.allclose(kernel_eigendecomp, kernel_direct)

# # Test the eigen-decomposition approach with the weighted Laplacian matrix
# eigenvalues_weighted, eigenvectors_weighted = np.linalg.eigh(weighted_laplacian)
# kernel_eigendecomp_weighted = laplacian_exponential_kernel_eigendecomp(eigenvalues_weighted, eigenvectors_weighted, t_test)
# # Output the first few elements of the weighted Laplacian matrix and the kernel as a sanity check
# print(f'weighted laplacian:\n {weighted_laplacian[:5, :5]}')
# print(f'Reconstructed Kernel from eigen-decomposition (weighted):\n {kernel_eigendecomp_weighted[:5, :5]}')

# # np.allclose(kernel_eigendecomp, kernel_direct)

# %%
# %%
################################################################# GRAPH PARAMETERS
# N = 100  # Number of nodes
# m = 3    # Number of edges to attach from a new node to existing nodes



# # SCALE FREE GRAPH
# scale_free_graph = nx.barabasi_albert_graph(N, m, seed=rand_seed)
# laplacian_matrix = nx.laplacian_matrix(scale_free_graph).toarray()
# # Assign random weights to each edge (for example, weights between 0.1 and 1.0)
# weighted_scale_free_graph = scale_free_graph.copy()
# for u, v in weighted_scale_free_graph.edges():
#     weighted_scale_free_graph[u][v]['weight'] = np.random.uniform(0.1, 1.0)

# weighted_split_scalefree_g, set_1, set_2 = adjust_inter_set_edge_weights(weighted_scale_free_graph, new_weight=0.01)

# # get hub nodes
# degree_dict = dict(scale_free_graph.degree(scale_free_graph.nodes()))
# # get 3 nodes with largest degree
# hub_nodes = sorted(degree_dict, key=lambda x: degree_dict[x], reverse=True)[:3]
# low_nodes = sorted(degree_dict, key=lambda x: degree_dict[x])[:3]
# print(f'hub nodes: {hub_nodes}')
# print(f'anti-hubs nodes: {low_nodes}')


# # RANDOM GRAPH
# random_graph = nx.erdos_renyi_graph(N, 0.5, seed=rand_seed) 
# random_laplacian = nx.laplacian_matrix(random_graph).toarray()
# weighted_random_graph = random_graph.copy()
# for u, v in weighted_random_graph.edges():
#     weighted_random_graph[u][v]['weight'] = 1.0


# %%
# %%
################################################################# GRAPH PARAMETERS
# N = 100  # Number of nodes
# m = 3    # Number of edges to attach from a new node to existing nodes



# # SCALE FREE GRAPH
# scale_free_graph = nx.barabasi_albert_graph(N, m, seed=rand_seed)
# laplacian_matrix = nx.laplacian_matrix(scale_free_graph).toarray()
# # Assign random weights to each edge (for example, weights between 0.1 and 1.0)
# weighted_scale_free_graph = scale_free_graph.copy()
# for u, v in weighted_scale_free_graph.edges():
#     weighted_scale_free_graph[u][v]['weight'] = np.random.uniform(0.1, 1.0)

# weighted_split_scalefree_g, set_1, set_2 = adjust_inter_set_edge_weights(weighted_scale_free_graph, new_weight=0.01)

# # get hub nodes
# degree_dict = dict(scale_free_graph.degree(scale_free_graph.nodes()))
# # get 3 nodes with largest degree
# hub_nodes = sorted(degree_dict, key=lambda x: degree_dict[x], reverse=True)[:3]
# low_nodes = sorted(degree_dict, key=lambda x: degree_dict[x])[:3]
# print(f'hub nodes: {hub_nodes}')
# print(f'anti-hubs nodes: {low_nodes}')


# # RANDOM GRAPH
# random_graph = nx.erdos_renyi_graph(N, 0.5, seed=rand_seed) 
# random_laplacian = nx.laplacian_matrix(random_graph).toarray()
# weighted_random_graph = random_graph.copy()
# for u, v in weighted_random_graph.edges():
#     weighted_random_graph[u][v]['weight'] = 1.0
