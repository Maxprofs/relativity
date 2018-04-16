import asyncio
import logging
import math
import numpy as np
import os
import pandas as pd
import sys

from datetime import date, datetime, timedelta

from nuget_api import can_ignore_exception, NugetCatalogClient, NugetContext
from serializer import CsvSerializer
from tagger import SmartTagger

from utils.iter import aenumerate, aislice
from utils.logging import log_call, StyleAdapter

LOG = StyleAdapter(logging.getLogger(__name__))

TOMORROW = datetime.fromordinal(
    (date.today() + timedelta(days=1)).toordinal()
)

SCHEMA = {
    'authors': object,
    'created': object,
    'description': object,
    'id': object,
    'is_prerelease': object, # bool (missing values)
    'last_updated': object,
    'listed': object, # bool (missing values)
    'missing_info': bool,
    'summary': object,
    'tags': object,
    'total_downloads': object, # np.int32 (missing values)
    'verified': object, # bool (missing values)
    'version': object,
}

async def write_packages(packages_root, args):
    log_call()
    os.makedirs(packages_root, exist_ok=True)
    async with NugetContext() as ctx:
        client = await NugetCatalogClient(ctx).load()
        page_start, page_end = args.page_start, args.page_start + (args.page_limit or sys.maxsize)
        pages = aislice(client.load_pages(), page_start, page_end)

        async for i, page in aenumerate(pages):
            pageno = page.pageno
            assert page_start + i == pageno
            LOG.debug("Fetching packages for page #{}", pageno)

            fname = os.path.join(packages_root, 'page{}.csv'.format(pageno))
            with CsvSerializer(fname) as writer:
                writer.write_header()
                packages = list(page.packages)
                results = await asyncio.gather(*[package.load() for package in packages],
                                               return_exceptions=True)
                for package, result in zip(packages, results):
                    if isinstance(result, Exception):
                        if not can_ignore_exception(result):
                            raise result
                    writer.write(package)

def read_packages(packages_root, args):
    DEFAULT_DATETIME = datetime(year=1900, month=1, day=1)
    DATE_FEATURES = ['created', 'last_updated']

    log_call()
    dfs = []
    start, end = args.page_start, args.page_start + (args.page_limit or sys.maxsize)

    for pageno in range(start, end):
        fname = os.path.join(packages_root, 'page{}.csv'.format(pageno))
        df = pd.read_csv(fname, dtype=SCHEMA, na_filter=False, parse_dates=DATE_FEATURES)

        # Remove entries with the same id, keeping the one with the highest version
        df['id_lower'] = df['id'].apply(str.lower)
        df.drop_duplicates(subset='id_lower', keep='last', inplace=True)
        df.drop('id_lower', axis=1, inplace=True)

        # Remove entries with missing_info = True, then set columns which no longer
        # have missing data to the correct type.
        df = df[~df['missing_info']]
        df['is_prerelease'] = df['is_prerelease'].astype(bool)
        df['listed'] = df['listed'].astype(bool)
        df['total_downloads'] = df['total_downloads'].astype(np.int32)
        df['verified'] = df['verified'].astype(bool)

        # Remove unlisted packages
        df = df[df['listed']]
        df.drop('listed', axis=1, inplace=True)

        # pandas doesn't do as it claims: it represents missing date values with 1900-01-01
        # rather than NaT as advertised. Correct that.
        for feature in DATE_FEATURES:
            df.loc[df[feature] == DEFAULT_DATETIME, feature] = math.nan

        df.reset_index(drop=True, inplace=True)
        dfs.append(df)

    return pd.concat(dfs, ignore_index=True)

def add_days_alive(df):
    log_call()
    pred = ~df['created'].isna()
    df.loc[~pred, 'days_alive'] = math.nan
    df.loc[pred, 'days_alive'] = df.loc[pred, 'created'].apply(lambda dt: max((TOMORROW - dt).days, 1))
    return df

def add_days_abandoned(df):
    log_call()
    pred = ~df['last_updated'].isna()
    df.loc[~pred, 'days_abandoned'] = math.nan
    df.loc[pred, 'days_abandoned'] = df.loc[pred, 'last_updated'].apply(lambda dt: max((TOMORROW - dt).days, 1))
    return df

def add_downloads_per_day(df):
    log_call()
    df['downloads_per_day'] = df['total_downloads'] / df['days_alive']
    df.loc[df['total_downloads'] == -1, 'downloads_per_day'] = math.nan
    return df

def add_etags(df):
    log_call()
    tagger = SmartTagger()
    df = tagger.fit_transform(df)
    return df, tagger

def dump_etags(df, fname, include_weights):
    def get_tag(etag):
        tag, _ = etag.split(' ')
        return tag

    log_call()
    m = df.shape[0]
    with open(fname, 'w', encoding='utf-8') as file:
        for index in range(m):
            id_, etags = df['id'][index], df['etags'][index]
            if not include_weights and etags:
                etags = ','.join(map(get_tag, etags.split(',')))
            line = "{}: {}\n".format(id_, etags)
            file.write(line)

async def load_packages(packages_root, args):
    if args.refresh_packages:
        await write_packages(packages_root, args)
    df = read_packages(packages_root, args)

    df = add_days_alive(df)
    df = add_days_abandoned(df)
    df = add_downloads_per_day(df)
    df, tagger = add_etags(df)

    if args.etags_fname is not None:
        dump_etags(df, fname=args.etags_fname, include_weights=args.include_weights)

    return df, tagger