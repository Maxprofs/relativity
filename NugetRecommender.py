import logging as log
import numpy as np
import pandas as pd

from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

def _compute_authors_scores(df):
    vectorizer = TfidfVectorizer(ngram_range=(2, 2))
    tfidf_matrix = vectorizer.fit_transform(df['authors'])
    return linear_kernel(tfidf_matrix, tfidf_matrix)

def _compute_description_scores(df):
    vectorizer = TfidfVectorizer(ngram_range=(1, 3),
                                 stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(df['description'])
    return linear_kernel(tfidf_matrix, tfidf_matrix)

def _compute_etags_scores(df, tags_vocab):
    # Let m be the number of packages and t be the number of tags.
    # Build an m x t matrix where M[i, j] represents the weight of package i along tag j.
    # Return an m x m matrix of cosine similarities.

    m = df.shape[0]
    tag_weights = pd.DataFrame(0, index=range(m), columns=sorted(tags_vocab))
    for index, etags in enumerate(df['etags']):
        for etag in etags.split(','):
            if not etag:
                continue
            tag, weight = etag.split()
            tag_weights[tag][index] = int(weight)

    tag_weights = csr_matrix(tag_weights.values)
    return linear_kernel(tag_weights, tag_weights)

def _compute_id_scores(df):
    vectorizer = TfidfVectorizer(ngram_range=(1, 2))
    tfidf_matrix = vectorizer.fit_transform(df['id'])
    return linear_kernel(tfidf_matrix, tfidf_matrix)

class NugetRecommender(object):
    def __init__(self,
                 tags_vocab,
                 weights={'authors': 1, 'description': 2, 'etags': 6, 'id': 3},
                 popularity_scale=.5):
        self.tags_vocab = tags_vocab
        self.weights = weights
        self.popularity_scale = popularity_scale

    def fit(self, df):
        # Let m be the number of packages. For each relevant feature like shared tags or similar names/descriptions,
        # compute a m x m matrix called M, where M[i, j] represents how relevant package j is to package i based on
        # that feature alone.
        # Set 'scores' to an m x m matrix of aggregate scores by taking a weighted average of these matrices.

        feature_scores = [
            _compute_authors_scores(df),
            _compute_description_scores(df),
            _compute_etags_scores(df, self.tags_vocab),
            _compute_id_scores(df),
        ]

        feature_weights = [
            self.weights['authors'],
            self.weights['description'],
            self.weights['etags'],
            self.weights['id'],
        ]

        scores = np.average(feature_scores, weights=feature_weights, axis=0)

        '''
        # Scale the scores according to popularity.
        ps = df['downloads_per_day'] / max(df['downloads_per_day'])
        for i in range(len(scores)):
            p = popularities[i]
            adjusted_p = p * 1 + (1 - p) * self.popularity_scale
            scores[:, i] *= adjusted_p
        '''

        # We don't want to recommend the same package based on itself, so set all scores along the diagonal to 0.
        for i in range(len(scores)):
            scores[i, i] = 0

        self._df = df
        self.scores_ = scores

    def predict(self, top_n):
        dict = {}
        for index, row in self._df.iterrows():
            id_ = self._df['id'][index]
            recommendation_indices = self.scores_[index].argsort()[:(-top_n - 1):-1]
            recommendations = [self._df['id'][i] for i in recommendation_indices]
            dict[id_] = recommendations

            if id_ in recommendations:
                log.debug("%s was in its own recommendation list!", id_)
                log.debug("Index of %s: %d", id_, index)
                log.debug("Recommendation indices for %s: %s", id_, recommendation_indices)
                log.debug("Recommendations for %s: %s", id_, recommendations)

        return dict