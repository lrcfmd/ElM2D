"""
A class to construct an ElM2D plot of a list of inorganic compostions based on
the Element Movers Distance Between These.


Copyright (C) 2021  Cameron Hargreaves

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>

--------------------------------------------------------------------------------

Python Parser Source: https://github.com/Zapaan/python-chemical-formula-parser

Periodic table JSON data: https://github.com/Bowserinator/Periodic-Table-JSON,
updated to include the Pettifor number and modified Pettifor number from
https://iopscience.iop.org/article/10.1088/1367-2630/18/9/093011

Network simplex source modified to use numba from
https://networkx.github.io/documentation/networkx-1.10/_modules/networkx/algorithms/flow/networksimplex.html#network_simplex

Requies umap which may be installed via
conda install -c conda-forge umap-learn
"""
import re, json, csv

from multiprocessing import Pool, cpu_count

from copy import deepcopy
from collections import Counter

import numpy as np
import pandas as pd
import pickle as pk 

from scipy.spatial.distance import squareform
from numba import njit 

import umap

import plotly.express as px
import plotly.io as pio

from tqdm import tqdm
from tqdm.contrib.concurrent import process_map  

from ElMD import ElMD, elmd

def main():
    from matminer.datasets import load_dataset
    df = load_dataset("matbench_expt_gap").head(1001)

    df_1 = df.head(500)
    df_2 = df.tail(500)
    mapper = ElM2D(metric="mod_petti")
    X = mapper.fit_transform(df_1["composition"])

    mapper.intersect(df_1["composition"], df_2["composition"])
    sorted_comps = mapper.sort(df["composition"])
    sorted_comps, sorted_inds = mapper.sort(df["composition"], return_inds=True)
    fts =  mapper.featurize()
    print()

class ElM2D():
    '''
    This class takes in a list of compound formula and creates the intercompound
    distance matrix wrt EMD and a two dimensional embedding using either PCA or 
    UMAP
    '''
    def __init__(self, formula_list=None,
                       n_proc=None,
                       n_components=2,
                       verbose=True,
                       metric="mod_petti",
                       chunksize=1,
                       umap_kwargs={}):

        self.verbose = verbose

        if n_proc is None:
            self.n_proc = cpu_count()
        else: 
            self.n_proc = n_proc

        self.formula_list = formula_list # Input formulae

        self.metric = metric
        self.chunksize=chunksize

        self.umap_kwargs = umap_kwargs

        self.umap_kwargs["n_components"] = n_components
        self.umap_kwargs["metric"] = "precomputed"

        self.input_mat = None    # Pettifor vector representation of formula
        self.embedder = None     # For accessing UMAP object
        self.embedding = None    # Stores the last embedded coordinates
        self.dm = None           # Stores distance matrix


    def save(self, filepath):
        # Save all variables except for the distance matrix
        save_dict = {k: v for k, v in self.__dict__.items()}
        f_handle = open(filepath + ".pk", 'wb')
        pk.dump(save_dict, f_handle)
        f_handle.close()
        
    def load(self, filepath):
        f_handle = open(filepath + ".pk", 'rb')
        load_dict = pk.load(f_handle)
        f_handle.close()

        for k, v in load_dict.items():
            self.__dict__[k] = v

    def plot(self, fp=None, color=None, embedding=None):
        if self.embedding is None:
            print("No embedding in memory, call transform() first.")    
            return 

        if embedding is None:
            embedding = self.embedding

        if embedding.shape[1] == 2:
            if color is None:
                df = pd.DataFrame({"x": embedding[:, 0], "y": embedding[:, 1], "formula": self.formula_list})
                fig = px.scatter(df, x="x", y="y", hover_name="formula", hover_data={"x":False, "y":False})

            else:
                df = pd.DataFrame({"x": embedding[:, 0], "y": embedding[:, 1], "formula": self.formula_list, color.name: color.to_numpy()})
                fig = px.scatter(df, x="x", y="y",color=color.name, hover_data={"formula": True, color.name: True, "x":False, "y":False})

        elif embedding.shape[1] == 3:
            if color is None:
                df = pd.DataFrame({"x": embedding[:, 0], "y": embedding[:, 1], "z": embedding[:, 2], "formula": self.formula_list})
                fig = px.scatter_3d(df, x="x", y="y", z="z", hover_name="formula", hover_data={"x":False, "y":False, "z":False})

            else:
                df = pd.DataFrame({"x": embedding[:, 0], "y": embedding[:, 1], "z": embedding[:, 2], "formula": self.formula_list, color.name: color.to_numpy()})
                fig = px.scatter_3d(df, x="x", y="y", z="z", color=color.name, hover_data={"formula": True, color.name: True, "x":False, "y":False, "z":False})
        
        elif embedding.shape[1] > 3:
            print("Too many dimensions to plot directly, using first three components")
            fig = self.plot(fp=fp, color=color, embedding=embedding[:, :3])
            return fig

        if fp is not None:
            pio.write_html(fig, fp)

        return fig

    def fit(self, X):
        '''
        Take an input vector, either of a precomputed distance matrix, or
        an iterable of strings of composition formula, construct an ElMD distance
        matrix and store to self.dm.

        Input
        X - A list of compound formula strings, or a precomputed distance matrix
        (ensure self.metric = "precomputed")
        '''
        self.formula_list = X
        n = len(X)

        if self.verbose: print(f"Fitting {self.metric} kernel matrix")
        if self.metric == "precomputed":
            self.dm = X

        elif n < 5:
            # Do this on a single core for smaller datasets
            distances = []
            print("Small dataset, using single CPU")
            for i in tqdm(range(n - 1)):
                x = ElMD(X[i], metric=self.metric)
                for j in range(i + 1, n):
                    distances.append(x.elmd(X[j]))
            
            dist_vec = np.array(distances)
            self.dm = squareform(dist_vec)

        else:
            if self.verbose: print("Constructing distances")
            dist_vec = self._process_list(X, n_proc=self.n_proc)
            self.dm = squareform(dist_vec)

    def fit_transform(self, X, y=None, how="UMAP", n_components=2):
        """
        Successively call fit and transform

        Parameters:
        X - List of compositions to embed 
        how - "UMAP" or "PCA", the embedding technique to use
        n_components - The number of dimensions to embed to
        """
        self.fit(X)
        embedding = self.transform(how=how, n_components=self.umap_kwargs["n_components"], y=y)
        return embedding

    def transform(self, how="UMAP", n_components=2, y=None):
        """
        Call the selected embedding method (UMAP or PCA) and embed to 
        n_components dimensions.
        """
        if self.dm is None:
            print("No distance matrix computed, run fit() first")
            return 

        if how == "UMAP":
            if y is None:
                if self.verbose: print(f"Constructing UMAP Embedding to {self.umap_kwargs['n_components']} dimensions")
                self.embedder = umap.UMAP(**self.umap_kwargs)
                self.embedding = self.embedder.fit_transform(self.dm)

            else:
                y = y.to_numpy(dtype=float)
                if self.verbose: print(f"Constructing UMAP Embedding to {self.umap_kwargs['n_components']} dimensions, with a targetted embedding")
                self.embedder = umap.UMAP(**self.umap_kwargs)
                self.embedding = self.embedder.fit_transform(self.dm, y)

        elif how == "PCA":
            if self.verbose: print(f"Constructing PCA Embedding to {self.umap_kwargs['n_components']} dimensions")
            self.embedding = self.PCA(n_components=self.umap_kwargs["n_components"])
            if self.verbose: print(f"Finished Embedding")

        return self.embedding

    def PCA(self, n_components=5):
        """
        Multidimensional Scaling - Given a matrix of interpoint distances,
        find a set of low dimensional points that have similar interpoint
        distances.
        https://github.com/stober/mds/blob/master/src/mds.py
        """

        if self.dm is None:
            raise Exception("No distance matrix computed, call fit_transform with a list of compositions, or load a saved matrix with load_dm()")
 

        (n,n) = self.dm.shape

        if self.verbose: print(f"Constructing {n}x{n_components} Gram matrix")
        # From Principles of Multivariate Analysis: A User's Perspective (page 107).
        # We construct a centred Gram Matrix to embed the dm into a high dim space
        E = (-0.5 * self.dm**2)

        # Use this matrix to get column and row means
        Er = np.mat(np.mean(E,1))
        Es = np.mat(np.mean(E,0))

        F = np.array(E - np.transpose(Er) - Es + np.mean(E))

        if self.verbose: print(f"Computing Eigen Decomposition")
        [U, S, V] = np.linalg.svd(F)

        Y = U * np.sqrt(S)

        if self.verbose: print(f"PCA Projected Points Computed")
        self.mds_points = Y

        return Y[:, :n_components]

    def sort(self, formula_list=None):
        """
        Sorts compositions based on their ElMD similarity.

        Usage:
        mapper = ElM2D()
        sorted_comps = mapper.sort(df["formula"])
        sorted_comps, sorted_inds = mapper.sort(df["formula"], return_inds=True)

        sorted_indices = mapper.sort()
        sorted_comps = mapper.sorted_comps
        """

        if formula_list is None and self.formula_list is None:
            raise Exception("Must input a list of compositions or fit a list of compositions first") # TODO Exceptions?

        elif formula_list is None:
            formula_list = self.formula_list

        elif self.formula_list is None:
            formula_list = process_map(ElMD, formula_list, chunksize=self.chunksize)
            self.formula_list = formula_list

        sorted_comps = sorted(formula_list)
        self.sorted_comps = sorted_comps

        return sorted_comps



    def cross_validate(self, y=None, X=None, k=5, shuffle=True, seed=42):
        """
        Implementation of cross validation with K-Folds.
        
        Splits the formula_list into k equal sized partitions and returns five 
        tuples of training and test sets. Returns a list of length k, each item 
        containing 2 (4 with target data) numpy arrays of formulae of 
        length n - n/k and n/k.

        Parameters:
            y=None: (optional) a numpy array of target properties to cross validate
            k=5: Number of k-folds
            shuffle=True: whether to shuffle the input formulae or not

        Usage:
            cvs = mapper.cross_validate()
            for i, (X_train, X_test) in enumerate(cvs):
                sub_mapper = ElM2D()
                sub_mapper.fit(X_train)
                sub_mapper.save(f"train_elm2d_{i}.pk")
                sub_mapper.fit(X_test)
                sub_mapper.save(f"test_elm2d_{i}.pk")
            ...
            cvs = mapper.cross_validate(y=df["target"])
            for X_train, X_test, y_train, y_test in cvs:
                clf.fit(X_train, y_train)
                y_pred = clf.predict(X_test)
                errors.append(mae(y_pred, y_test))
            print(np.mean(errors))
        """
        inds = np.arange(len(self.formula_list)) # TODO Exception

        if shuffle:
            np.random.seed(seed) 
            np.random.shuffle(inds)

        if X is None:
            formulas = self.formula_list.to_numpy(str)[inds]
        
        else:
            formulas = X
        
        splits = np.array_split(formulas, k)

        X_ret = []
        
        for i in range(k):
            train_splits = np.delete(np.arange(k), i)
            X_train = splits[train_splits[0]]

            for index in train_splits[1:]:
                X_train = np.concatenate((X_train, splits[index]))
            
            X_test = splits[i]
            X_ret.append((X_train, X_test))

        if y is None:
            return X_ret

        y = y.to_numpy()[inds]
        y_splits = np.array_split(y, k)
        y_ret = []

        for i in range(k):
            train_splits = np.delete(np.arange(k), i)
            y_train = y_splits[train_splits[0]]

            for index in train_splits[1:]:
                y_train = np.concatenate((y_train, y_splits[index]))
            
            y_test = y_splits[i]
            y_ret.append((y_train, y_test))

        return [(X_ret[i][0], X_ret[i][1], y_ret[i][0], y_ret[i][1]) for i in range(k)]

    def _process_list(self, formula_list, n_proc):
        '''
        Given an iterable list of formulas in composition form
        use multiple processes to convert these to pettifor ratio
        vector form, and then calculate the distance between these
        pairings, returning this as a condensed distance vector
        '''
        pool_list = []

        # n_elements = len(ElMD().periodic_tab)
        self.input_mat = [None for x in formula_list]

        if self.verbose: 
            print("Parsing Formula")
            for i, formula in tqdm(list(enumerate(formula_list))):
                if isinstance(formula, str):
                    self.input_mat[i] = ElMD(formula, metric=self.metric)
                elif isinstance(formula, ElMD):
                    self.input_mat[i] = formula
                else:
                    raise TypeError("Input must be either compositional strings or ElMD objects")
        else:
            for i, formula in enumerate(formula_list):
                if isinstance(formula, str):
                    self.input_mat[i] = ElMD(formula, metric=self.metric)
                elif isinstance(formula, ElMD):
                    self.input_mat[i] = formula
                else:
                    raise TypeError("Input must be either compositional strings or ElMD objects")

        # Create input pairings
        if self.verbose: 
            print("Constructing joint compositional pairings")
            for i in tqdm(range(len(formula_list) - 1)):
                sublist = [(i, j) for j in range(i + 1, len(formula_list))]
                pool_list.append(sublist)
        else:
            for i in range(len(formula_list) - 1):
                sublist = [(i, j) for j in range(i + 1, len(formula_list))]
                pool_list.append(sublist)

        # Distribute amongst processes
        if self.verbose: print("Creating Process Pool\nScattering compositions between processes and computing distances")
        
        scores = process_map(self._pool_ElMD, pool_list, chunksize=self.chunksize)
        
        if self.verbose: print("Distances computed closing processes")

        if self.verbose: print("Flattening sublists")
        # Flattens list of lists to single list
        distances = [dist for sublist in scores for dist in sublist]
       
        return np.array(distances, dtype=np.float64)

    def _pool_ElMD(self, input_tuple):
        '''
        Uses multiprocessing module to call the numba compiled EMD function
        '''
        distances = np.ndarray(len(input_tuple))
        # elmd_obj = ElMD(metric=self.metric)
        
        for i, (input_1, input_2) in enumerate(input_tuple):
            distances[i] = elmd(self.input_mat[input_1], 
                               self.input_mat[input_2],
                               metric=self.metric)

        return distances

    def __repr__(self):
        if self.dm is not None:
            return f"ElM2D(size={len(self.formula_list)},  chemical_diversity={np.mean(self.dm)} +/- {np.std(self.dm)}, maximal_distance={np.max(self.dm)})"
        else:
            return f"ElM2D()"

    def export_dm(self, path):
        np.savetxt(path, self.dm, delimiter=",")
        
    def import_dm(self, path):
        self.dm = np.loadtxt(path, delimiter=",")

    def export_embedding(self, path):
        np.savetxt(path, self.embedding, delimiter=",")
        
    def import_embedding(self, path):
        self.embedding = np.loadtxt(path, delimiter=",")

    def _pool_featurize(self, comp):
        return ElMD(comp, metric=self.metric).feature_vector

    def featurize(self, formula_list=None, how="mean"):
        if formula_list is None and self.formula_list is None:
            raise Exception("You must enter a list of compositions first")

        elif formula_list is None:
            formula_list = self.formula_list

        elif self.formula_list is None:
            self.formula_list = formula_list

        elmd_obj = ElMD(metric=self.metric)

        # if type(elmd_obj.periodic_tab[self.metric]["H"]) is int:
        #     vectors = np.ndarray((len(compositions), len(elmd_obj.periodic_tab[self.metric])))
        # else:
        #     vectors = np.ndarray((len(compositions), len(elmd_obj.periodic_tab[self.metric]["H"])))

        print(f"Constructing compositionally weighted {self.metric} feature vectors for each composition")
        vectors = process_map(self._pool_featurize, formula_list, chunksize=self.chunksize)

        print("Complete")

        return np.array(vectors)

    def intersect(self, y=None, X=None):
        """
        Takes in a second formula list, y, and computes the intersectional distance 
        matrix between the two under the given metric. If a two formula lists
        are given the intersection between the two is computed, returning a 
        distance matrix of the form:

              X_0             X_1            X_2            ...
        y_0  ElMD(X_0, y_0)  ElMD(X_1, y_0)  ElMD(X_2, y_0)
        y_1  ElMD(X_0, y_1)  ElMD(X_1, y_1)  ElMD(X_2, y_1)
        f_2  ElMD(X_0, y_2)  ElMD(X_1, y_2)  ElMD(X_2, y_2)
        ...
        """
        if X is None and y is None:
            raise Exception("Must enter two lists of formula or fit a list of formula and enter an intersecting list of formula")
        if X is None:
            X = self.formula_list

        elmd = ElMD()
        dm = []
        process_pool = Pool(cpu_count())

        for comp_1 in tqdm(X):
            ionic = process_pool.starmap(elmd.elmd, ((comp_1, comp_2) for comp_2 in y))
            dm.append(ionic)
        
        distance_matrix = np.array(dm)

        return distance_matrix

        # Not working currently, might be faster...
        # intersection_dm = self._process_intersection(X, y, self.n_proc)

        # return intersection_dm
        

    def _process_intersection(self, X, y, n_proc):
        '''
        Compute the intersection of two lists of compositions
        '''
        pool_list = []

        n_elements = len(ElMD().periodic_tab)
        X_mat = np.ndarray(shape=(len(X), n_elements), dtype=np.float64)
        y_mat = np.ndarray(shape=(len(y), n_elements), dtype=np.float64)

        if self.verbose: print("Parsing X Formula")
        for i, formula in tqdm(list(enumerate(X))):
            X_mat[i] = ElMD(formula, metric=self.metric).ratio_vector

        if self.verbose: print("Parsing Y Formula")
        for i, formula in tqdm(list(enumerate(y))):
            y_mat[i] = ElMD(formula, metric=self.metric).ratio_vector

        # Create input pairings
        if self.verbose: print("Constructing joint compositional pairings")
        for y in tqdm(range(len(y_mat))):
            sublist = [(y, x) for x in range(len(X_mat))]
            pool_list.append(sublist)
        
        # Distribute amongst processes
        if self.verbose: print("Creating Process Pool\nScattering compositions between processes and computing distances")
        distances = process_map(self._pool_ElMD, pool_list, chunksize=self.chunksize)

        if self.verbose: print("Distances computed closing processes")

        # if self.verbose: print("Flattening sublists")
        # Flattens list of lists to single list
        # distances = [dist for sublist in scores for dist in sublist]
       
        return np.array(distances, dtype=np.float64)

if __name__ == "__main__":
    main()
