# %%
import networkx as nx
import numpy as np
import scipy.linalg
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import math
import random
import copy


# %%
# Step 2: Adjust Laplacian Matrix Calculation for Weighted Graph
def weighted_laplacian_matrix(G):
    """
    Calculate the Laplacian matrix for a weighted graph.
    """
    # Weighted adjacency matrix
    W = nx.to_numpy_array(G, weight='weight')
    # Diagonal matrix of vertex strengths
    D = np.diag(W.sum(axis=1))
    # Weighted Laplacian matrix
    L = D - W
    return L


def laplacian_exponential_kernel_eigendecomp(eigenvalues, eigenvectors, t):
    """
    Function to compute the Laplacian exponential diffusion kernel using eigen-decomposition
    """
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

def knockdown_node(G, node_to_isolate, reduction_factor=0.5):
    """
    Reduces the weights of all edges connected to a node in the graph.

    :param G: NetworkX graph
    :param node_to_isolate: Node whose edges will be reduced
    :param reduction_factor: Factor to reduce edge weights by, defaults to 0.5
    :return: Tuple containing the modified graph and its weighted Laplacian matrix
    """
    modified_graph = G.copy()
    # Reduce the weight of all edges to and from this node
    for neighbor in G[node_to_isolate]:
        # Reduce the weight by the reduction factor
        modified_graph[node_to_isolate][neighbor]['weight'] *= reduction_factor
        modified_graph[neighbor][node_to_isolate]['weight'] *= reduction_factor
    
    # Compute the weighted Laplacian matrix for the modified graph
    new_laplacian = weighted_laplacian_matrix(modified_graph)

    return modified_graph, new_laplacian

# %%
# generate a random integer from 0 to 100
rand_seed = random.randint(0, 100)
print(f'random seed: 43 not {rand_seed}')
# set random seed to that number
np.random.seed(43)
random.seed(43)

# PARAMETERS
N = 25  # Number of nodes
m = 1    # Number of edges to attach from a new node to existing nodes

t_values = np.linspace(0.01, 10, 500)
fixed_reduction = 0.05
node_to_isolate = np.random.randint(0, N - 1)

# SCALE FREE GRAPH
scale_free_graph = nx.barabasi_albert_graph(N, m)
laplacian_matrix = nx.laplacian_matrix(scale_free_graph).toarray()
# Assign random weights to each edge (for example, weights between 0.1 and 1.0)
weighted_scale_free_graph = scale_free_graph.copy()
for u, v in weighted_scale_free_graph.edges():
    weighted_scale_free_graph[u][v]['weight'] = np.random.uniform(0.1, 1.0)
weighted_scalefree_laplacian = weighted_laplacian_matrix(weighted_scale_free_graph)

# RANDOM GRAPH
random_graph = nx.erdos_renyi_graph(N, 0.5) 
random_laplacian = nx.laplacian_matrix(random_graph).toarray()
weighted_random_graph = random_graph.copy()
for u, v in weighted_random_graph.edges():
    weighted_random_graph[u][v]['weight'] = 1.0
weighted_random_laplacian = weighted_laplacian_matrix(weighted_random_graph)

weighted_graph_use = weighted_random_graph




# %%
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 6), sharey=True, sharex=True, dpi=300)

max_gdds = []
max_gdd_times = []

original_weighted_graph_use = copy.deepcopy(weighted_graph_use)
for reduction in np.linspace(0.1, 0.9, 9):
    # kernels = [laplacian_exponential_diffusion_kernel(laplacian_matrix, t) for t in t_values]

    # NODE KNOCKOUT
    # Remove the node and recompute the Laplacian matrix
    weighted_graph_use = copy.deepcopy(original_weighted_graph_use)
    weighted_lap_use = weighted_laplacian_matrix(weighted_graph_use)

    knockdown_graph, knockdown_laplacian = knockdown_node(weighted_graph_use, node_to_isolate, reduction_factor=reduction)

    # CALCULATE GDD
    # Compute the Frobenius norm of the difference between the kernels for each t
    gdd_values = [np.linalg.norm(laplacian_exponential_diffusion_kernel(weighted_lap_use, t) -
                                laplacian_exponential_diffusion_kernel(knockdown_laplacian, t), 'fro') 
                                for t in t_values]

    # Finding the maximum GDD value
    max_gdd = max(gdd_values)
    max_gdd_index = gdd_values.index(max_gdd)
    max_gdd_time = t_values[max_gdd_index]

    max_gdds.append(max_gdd)
    max_gdd_times.append(max_gdd_time)


    # # EDGE KNOCKOUT
    # edges_list = list(weighted_graph_use.edges())
    # random_index = random.randint(0, len(edges_list) - 1)
    # edge_to_isolate = edges_list[random_index]
    # de_edged_graph = weighted_graph_use.copy()
    # de_edged_graph.remove_edge(*edge_to_isolate)
    # de_edged_laplacian = weighted_laplacian_matrix(de_edged_graph)

    # edge_gdd_values = [np.linalg.norm(laplacian_exponential_diffusion_kernel(laplacian_matrix, t) -
    #                                 laplacian_exponential_diffusion_kernel(de_edged_laplacian, t), 'fro')
    #                                 for t in t_values]

    if True:
        if False: 
            ax1.plot(t_values, edge_gdd_values, label='GDD(t) for EDGE KNOCKOUT')
        if np.round(reduction, 2) != fixed_reduction:
            ax1.plot(t_values, gdd_values, label=f'{round(reduction, 2)}', alpha=0.65)
        else:
            print(max_gdd)
            ax1.plot(t_values, gdd_values, label=f'{round(reduction, 2)}', alpha = 1, color='black', linewidth=2)
        # ax1.plot(max_gdd_time, max_gdd, 'ro', label='Max GDD')
        # add a text label at maximum point
        # ax1.annotate(f'Max GDD = {round(max_gdd, 2)}\n at t = {round(max_gdd_time, 2)}', xy=(max_gdd_time, max_gdd), xytext=(max_gdd_time + 0.01, max_gdd - 0.01))
        ax1.set_xlabel('Diffusion Time (t)', fontsize=15)
        ax1.set_ylabel('GDD Value (Graph Difference)', fontsize=15)
        ax1.set_title(f'GDD Values per Reduction Factor, NODE: {node_to_isolate}', fontsize=15)
        ax1.legend(loc='upper right', title='Knockdown \nReduction Factor')
        ax1.grid(True)

ax1.plot(max_gdd_times, max_gdds, 'r', label='Max GDD')

for node in range(N):
    weighted_graph_use = copy.deepcopy(original_weighted_graph_use)
    weighted_lap_use = weighted_laplacian_matrix(weighted_graph_use)
    knockdown_graph2, knockdown_laplacian2 = knockdown_node(weighted_graph_use, node, reduction_factor=fixed_reduction)
    # CALCULATE GDD
    # Compute the Frobenius norm of the difference between the kernels for each t
    gdd_values_fixed = [np.linalg.norm(laplacian_exponential_diffusion_kernel(weighted_lap_use, t) -
                                       laplacian_exponential_diffusion_kernel(knockdown_laplacian2, t), 'fro') 
                        for t in t_values]
    if node != node_to_isolate:
        ax2.plot(t_values, gdd_values_fixed, label=f'{node}', alpha=0.8)
    else:
        print(f'maximum GDD: {max(gdd_values_fixed)}, time: {t_values[gdd_values_fixed.index(max(gdd_values_fixed))]}')
        choice_gdd = gdd_values_fixed

ax2.plot(t_values, choice_gdd, label=f'{node}', alpha = 1, color='black', linewidth=2)
ax2.set_xlabel('Diffusion Time (t)', fontsize=15)
# ax2.set_ylabel('GDD Value (Graph Difference)', fontsize=15)
ax2.set_xlim(ax1.get_xlim())
ax2.set_ylim(ax1.get_ylim())
ax2.set_title(f'GDD Values per Node Knockdown, reduction={fixed_reduction}', fontsize=15)
ax2.legend(loc='upper right', title='Knocked Node')
ax2.grid(True)


plt.show()






# %%
def plot_diffusion_process_for_two_graphs(graphs, laplacians, times, node_to_isolate, start_node=0):
    """
    Plots the diffusion process on two graphs at specified times from a single starting node.

    :param graphs: List of two NetworkX graphs
    :param laplacians: List of two Laplacian matrices corresponding to the graphs
    :param times: List of 3 times at which to visualize the diffusion
    :param start_node: Node from which the diffusion starts
    """
    if len(graphs) != 2 or len(times) != 3:
        print(len(graphs), len(times))
        raise ValueError("Function requires exactly two graphs and three time points.")
    
    fig, axes = plt.subplots(2, 3, figsize=(15, 10))  # 2 rows, 3 columns
    fig.suptitle(f'Unperturbed graph (top), Knockdown of node {node_to_isolate} by factor {fixed_reduction} (bottom)', fontsize=25)


    layout=nx.spring_layout(graphs[0])
    label_layout = {node: (x - 0.1, y) for node, (x, y) in layout.items()}  # Shift labels to the left

    for i, (G, L) in enumerate(zip(graphs, laplacians)):
        for j, t in enumerate(times):
            kernel = laplacian_exponential_diffusion_kernel(L, t)
            heat_values = kernel[start_node, :]

            if i == 1:  # If it's the second graph
                # get index of smallest heat value
                print(f'node with lowest heat == isolated node: {np.argmin(heat_values) == node_to_isolate}')
                sorted_heat_values = np.sort(heat_values)
                second_smallest_value = sorted_heat_values[1]  # Select the second smallest value
                norm = mcolors.Normalize(vmin=second_smallest_value, vmax=max(heat_values), clip=True)
                heat_values[node_to_isolate] = sorted_heat_values[0]
            else:
                norm = mcolors.Normalize(vmin=min(heat_values), vmax=max(heat_values), clip=True)

            # norm = mcolors.Normalize(vmin=min(heat_values), vmax=max(heat_values), clip=True)
            mapper = plt.cm.ScalarMappable(norm=norm, cmap=plt.cm.viridis)

            nx.draw_networkx_edges(G, layout, alpha=0.2, ax=axes[i, j])
            nx.draw_networkx_nodes(G, layout, node_size=200,
                                   node_color=[mapper.to_rgba(value) for value in heat_values],
                                   ax=axes[i, j])
            # set label positions to the left of their respective nodes

            nx.draw_networkx_labels(G, label_layout, font_size=20, ax=axes[i, j])
            axes[i, j].set_title(f"Graph {i+1}, t={round(t, 2)}", fontsize=20)
            axes[i, j].axis('off')

    plt.colorbar(mapper, ax=axes[1, 2], shrink=0.7, aspect=20, pad=0.02)
    plt.tight_layout()
    plt.show()

# Example usage
t_values = [0.1, max_gdd_times[node_to_isolate], 10]

weighted_graph_use = copy.deepcopy(original_weighted_graph_use)
knockdown_graph, knockdown_laplacian = knockdown_node(weighted_graph_use, node_to_isolate, reduction_factor=fixed_reduction)

# Example usage with 3 graphs and their Laplacians for 9 different times
plot_diffusion_process_for_two_graphs([weighted_graph_use, knockdown_graph], 
                                           [weighted_random_laplacian, knockdown_laplacian], 
                                           t_values, start_node=0, node_to_isolate=node_to_isolate)





# %% ############################ BARBELL GRAPHS ############################
# Let's generate the three barbell graphs as shown in the image.
# The first graph will be a complete barbell graph, the second will have its bridge removed, 
# and the third will have its central connection removed.

# Define the number of nodes in the barbell graph's complete subgraphs and the bridge length
m1 = 5  # Number of nodes in the complete subgraphs
m2 = 0  # Number of nodes in the bridge

# Generate the complete barbell graph
G_single_edge = nx.barbell_graph(m1, m2)

# Identify the nodes to move and disconnect
node_to_move_from_first_bell = m1 - 2  # Second to last node in the first bell
node_to_move_from_second_bell = m1 + 1  # Second node in the second bell

G_complete = G_single_edge.copy()
# Add the new edge directly connecting the two identified nodes
G_complete.add_edge(node_to_move_from_first_bell, node_to_move_from_second_bell)

G_cc = G_complete.copy()
# remove edge between nodes 7 and 9
G_cc.remove_edge(7, 9)

# Verify the graphs by plotting them
fig, axes = plt.subplots(1, 3, figsize=(18, 6))
# Plot the complete barbell graph
nx.draw(G_complete, ax=axes[0], with_labels=True)
axes[0].set_title('Complete Barbell Graph')
# Plot the barbell graph with the bridge removed
nx.draw(G_single_edge, ax=axes[1], with_labels=True)
axes[1].set_title('Barbell Graph with Bridge Removed')
# Plot the barbell graph with the bell connection removed
nx.draw(G_cc, ax=axes[2], with_labels=True)
axes[2].set_title('Barbell Graph with bell connection removed')

plt.tight_layout()
plt.show()



# %% PLOTTING THE MAX GDD
# Compute the Laplacian Matrices for both graphs
laplacian_A = weighted_laplacian_matrix(G_complete)
laplacian_B = weighted_laplacian_matrix(G_cc)

# Step 3 and 4: Compute the Laplacian Kernels and calculate the xi values over a range of t values
t_values = np.linspace(0, 10, 1000)
xi_values = []

for t in t_values:
    kernel_A = laplacian_exponential_kernel_eigendecomp(*np.linalg.eigh(laplacian_A), t)
    kernel_B = laplacian_exponential_kernel_eigendecomp(*np.linalg.eigh(laplacian_B), t)
    xi = np.linalg.norm((kernel_A - kernel_B), 'fro')
    xi_values.append(xi)

# Find the maximum xi value and the corresponding t value using line search
max_xi = max(xi_values)
max_xi_index = xi_values.index(max_xi)
max_xi_time = t_values[max_xi_index]

# Step 5: Plot xi against t
plt.figure(figsize=(10, 6))
plt.plot(t_values, xi_values, label='xi(t)')
plt.plot(max_xi_time, max_xi, 'ro', label='Max xi')
# add a text label at maximum point
plt.annotate(f'Max xi = {round(max_xi, 2)}\n at t = {round(max_xi_time, 2)}', xy=(max_xi_time, max_xi), xytext=(max_xi_time + 1, max_xi - 0.1),
             )
plt.xlabel('Diffusion Time (t)')
plt.ylabel('Xi Value (Graph Difference)')
plt.title('Xi Values Over Diffusion Time for Two Graphs')
plt.legend()
plt.grid(True)
plt.show()


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