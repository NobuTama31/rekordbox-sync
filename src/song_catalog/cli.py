import argparse
import json

from .catalog import CatalogError, CsvSongCatalog


def main(argv=None):
    parser = argparse.ArgumentParser(description="Look up CSV song metadata by title and artist")
    parser.add_argument("csv")
    parser.add_argument("--title", required=True)
    parser.add_argument("--artist", required=True)
    parser.add_argument("--field", action="append", dest="fields")
    parser.add_argument("--title-column", default="タイトル")
    parser.add_argument("--artist-column", default="歌手")
    args = parser.parse_args(argv)
    try:
        catalog = CsvSongCatalog(args.csv, title_column=args.title_column, artist_column=args.artist_column)
        result = catalog.lookup(args.title, args.artist, fields=args.fields)
    except (CatalogError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status == "matched" else 1
