#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Eksport gazu z Polski według kraju przeznaczenia.
API DBW GUS.

CN:
27111100 - gaz ziemny skroplony LNG
27112100 - gaz ziemny w stanie gazowym

Wyniki:
gus_gaz_export/
    gus_gaz_export_miesieczne.csv
    gus_gaz_export_roczne.csv
    raport_pobierania_export.csv
    metadane_gus_export.json
    cache/
"""

import argparse
import calendar
import csv
import gzip
import json
import re
import sys
import time

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ============================================================
# USTAWIENIA
# ============================================================

API = "https://api-dbw.stat.gov.pl/api/1.2.0/"

# Eksport towarów
ID_ZMIENNEJ = 220

# Polska; Kraje towary; CN - uzupełniająca jednostka miary
ID_PRZEKROJU = 1434

KODY_CN = {
    "27111100",
    "27112100",
}

ROK_OD = 2018
ROK_DO = None

ROZMIAR_STRONY = 5000


# ============================================================
# KLUCZ API
# ============================================================

# WKLEJ TU DOKŁADNIE TEN SAM KLUCZ,
# KTÓRY DZIAŁA W TWOIM SKRYPCIE IMPORTOWYM.
# NIE USUWAJ KOŃCOWEGO "=".

KLUCZ_API = "rd0rSweA0HdXUfrNpJB5U6vypciTFcvBzuM8kRFarVU="


# ============================================================
# POZOSTAŁE USTAWIENIA
# ============================================================

KATEGORIE_SPECJALNE = {
    "QP",
    "QQ",
    "QR",
    "QS",
    "QU",
    "QV",
    "QW",
    "QX",
    "QY",
    "QZ",
    "_no",
}


KOLUMNY = [
    "okres",
    "rok",
    "miesiac",
    "kraj",
    "kod_kraju",
    "typ_kraju",
    "kod_cn",
    "towar",
    "wartosc",
    "jednostka",
    "status",
    "flaga_gus",
    "tajnosc_gus",
    "brak_wartosci_gus",
    "wartosc_opisowa_gus",
    "precyzja_gus",
    "id_kraju_gus",
    "id_cn_gus",
    "id_okresu_gus",
]


# ============================================================
# ZAPIS
# ============================================================

def zapisz_json(sciezka, dane):

    sciezka.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = sciezka.with_suffix(
        sciezka.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            dane,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    tmp.replace(sciezka)


def zapisz_csv(sciezka, kolumny, wiersze):

    sciezka.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = sciezka.with_suffix(".csv.tmp")

    with tmp.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=kolumny,
            delimiter=";"
        )

        writer.writeheader()
        writer.writerows(wiersze)

    tmp.replace(sciezka)


# ============================================================
# KLIENT GUS
# ============================================================

class KlientGUS:

    def __init__(self, klucz):

        self.klucz = klucz.strip()
        self.ostatnie_zadanie = 0.0

        if not self.klucz:

            raise RuntimeError(
                "KLUCZ_API jest pusty."
            )

        if self.klucz == "TU_WKLEJ_SWOJ_KLUCZ_GUS":

            raise RuntimeError(
                "Wklej swój klucz GUS w zmiennej KLUCZ_API."
            )


    def pobierz(
        self,
        endpoint,
        parametry=None,
        dopuszczaj_404=False
    ):

        url = (
            API
            + endpoint
            + "?"
            + urlencode(
                {
                    "lang": "pl",
                    **(parametry or {})
                }
            )
        )

        for proba in range(6):

            # ograniczenie liczby zapytań
            time.sleep(
                max(
                    0,
                    2.05
                    - (
                        time.monotonic()
                        - self.ostatnie_zadanie
                    )
                )
            )

            self.ostatnie_zadanie = time.monotonic()

            naglowki = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-ClientId": self.klucz,
                "User-Agent": "GUS-DBW-gaz-export/1.0",
            }

            try:

                request = Request(
                    url,
                    headers=naglowki
                )

                with urlopen(
                    request,
                    timeout=90
                ) as response:

                    tresc = response.read()

                    if (
                        response.headers.get(
                            "Content-Encoding"
                        )
                        == "gzip"
                    ):

                        tresc = gzip.decompress(
                            tresc
                        )

                    return json.loads(
                        tresc.decode(
                            "utf-8-sig"
                        )
                    )


            except HTTPError as e:

                if (
                    e.code == 404
                    and dopuszczaj_404
                ):

                    return None


                if e.code == 401:

                    raise RuntimeError(
                        "HTTP 401 - nieprawidłowy klucz API GUS. "
                        "Sprawdź KLUCZ_API."
                    ) from None


                if e.code == 403:

                    raise RuntimeError(
                        "HTTP 403 - GUS odrzucił dostęp. "
                        "Sprawdź klucz API."
                    ) from None


                if e.code not in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504
                ):

                    raise RuntimeError(
                        f"HTTP {e.code}: {endpoint}"
                    ) from None


                powod = f"HTTP {e.code}"


            except (
                URLError,
                TimeoutError,
                OSError,
                ValueError
            ) as e:

                powod = type(e).__name__


            if proba == 5:

                raise RuntimeError(
                    f"Nie udało się pobrać {endpoint}: {powod}"
                )


            opoznienie = (
                120
                if powod == "HTTP 429"
                else min(
                    60,
                    5 * 2 ** proba
                )
            )

            print(
                f"{powod}; ponawiam za "
                f"{opoznienie} s.",
                flush=True
            )

            time.sleep(opoznienie)


    def slownik(self, nazwa, klucz):

        wynik = {}
        strona = 1

        while True:

            dane = self.pobierz(
                "dictionaries/" + nazwa,
                {
                    "page": strona,
                    "page-size": ROZMIAR_STRONY
                }
            )

            if (
                not isinstance(dane, dict)
                or
                not isinstance(
                    dane.get("data"),
                    list
                )
            ):

                raise RuntimeError(
                    f"Błąd słownika: {nazwa}"
                )


            wynik.update(
                {
                    x[klucz]: x
                    for x in dane["data"]
                }
            )


            if strona >= dane["page-count"]:

                return wynik


            strona += 1


# ============================================================
# METADANE
# ============================================================

def wczytaj_metadane(klient):

    meta = klient.pobierz(
        "variable/variable-meta",
        {
            "id-zmiennej": ID_ZMIENNEJ
        }
    )


    nazwa = meta.get(
        "nazwa",
        ""
    )


    print(
        f"Zmienna GUS: {nazwa}"
    )


    if "eksport" not in nazwa.casefold():

        raise RuntimeError(
            f"ID_ZMIENNEJ={ID_ZMIENNEJ} "
            f"nie jest eksportem. "
            f"GUS zwrócił: {nazwa!r}"
        )


    pozycje = klient.pobierz(
        "variable/variable-section-position",
        {
            "id-przekroj": ID_PRZEKROJU
        }
    )


    if not isinstance(pozycje, list):

        raise RuntimeError(
            "Nie udało się pobrać pozycji przekroju."
        )


    # ========================================================
    # KRAJE
    # ========================================================

    kraje = {

        x["id-pozycja"]: x

        for x in pozycje

        if (
            x.get("nazwa-wymiar")
            == "Kraje towary"

            and x.get("symbol")
            not in (
                "00",
                "EU"
            )

            and x.get(
                "nazwa-pozycja",
                ""
            )
            .strip()
            .casefold()
            != "ogółem"
        )
    }


    if not kraje:

        raise RuntimeError(
            "Nie znaleziono krajów w przekroju."
        )


    # ========================================================
    # TOWARY
    # ========================================================

    towary = {

        x["id-pozycja"]: dict(x)

        for x in pozycje

        if (
            "CN"
            in x.get(
                "nazwa-wymiar",
                ""
            )

            and str(
                x.get(
                    "symbol",
                    ""
                )
            )
            in KODY_CN
        )
    }


    znalezione_cn = {

        str(
            x.get("symbol")
        )

        for x in towary.values()
    }


    if znalezione_cn != KODY_CN:

        raise RuntimeError(
            f"Nie znaleziono obu kodów CN. "
            f"Znaleziono: {znalezione_cn}"
        )


    # ========================================================
    # JEDNOSTKI
    # ========================================================

    for x in towary.values():

        nazwa_towaru = x.get(
            "nazwa-pozycja",
            ""
        )

        jednostka = re.search(
            r"\[([^\[\]]+)\]\s*$",
            nazwa_towaru
        )

        if jednostka:

            x["jednostka"] = (
                jednostka.group(1)
            )

        else:

            x["jednostka"] = ""


    # ========================================================
    # OKRESY
    # ========================================================

    okresy = klient.slownik(
        "periods-dictionary",
        "id-okres"
    )


    miesiace = {}


    for ident, x in okresy.items():

        m = re.fullmatch(
            r"M(\d{2})",
            x.get(
                "symbol",
                ""
            )
        )

        if (
            x.get(
                "id-czestotliwosc"
            )
            == 3

            and x.get(
                "id-typ"
            )
            == 1

            and m
        ):

            miesiace[
                int(
                    m.group(1)
                )
            ] = ident


    roczne = [

        ident

        for ident, x
        in okresy.items()

        if (
            x.get("opis")
            ==
            "rok - dane roczne - rok"
        )
    ]


    if set(miesiace) != set(range(1, 13)):

        raise RuntimeError(
            "Nie znaleziono wszystkich 12 miesięcy."
        )


    if len(roczne) != 1:

        raise RuntimeError(
            "Nie rozpoznano okresu rocznego."
        )


    # ========================================================
    # SERIE
    # ========================================================

    serie = {

        x["id-czestotliwosc"]: x

        for x in meta.get(
            "przekroje",
            []
        )

        if (
            x.get("id-przekroj")
            == ID_PRZEKROJU
        )
    }


    if not {
        1,
        3
    }.issubset(serie):

        raise RuntimeError(
            "Brak miesięcznej albo rocznej "
            "serii dla przekroju."
        )


    return {
        "zrodlo": "GUS, DBW, CC BY 4.0",
        "api": API,
        "zmienna": meta,
        "kraje": kraje,
        "towary": towary,
        "miesiace": miesiace,
        "okres_roczny": roczne[0],
        "serie": serie,

        "flagi": klient.slownik(
            "flag-dictionary",
            "id-flaga"
        ),

        "braki": klient.slownik(
            "no-value-dictionary",
            "id-brak-wartosci"
        ),

        "tajnosc": klient.slownik(
            "confidentionality-dictionary",
            "id-tajnosci"
        ),
    }


# ============================================================
# ODCZYT WYMIARU
# ============================================================

def pozycja_wymiaru(
    wiersz,
    wymiar
):

    for i in range(
        1,
        16
    ):

        if (
            wiersz.get(
                f"id-wymiar-{i}"
            )
            == wymiar
        ):

            return wiersz[
                f"id-pozycja-{i}"
            ]


    raise RuntimeError(
        f"Brakuje wymiaru {wymiar}."
    )


# ============================================================
# POBIERANIE OKRESU
# ============================================================

def pobierz_okres(
    klient,
    meta,
    rok,
    miesiac,
    cache,
    odswiez=False
):

    okres = (

        meta["miesiace"][miesiac]

        if miesiac

        else meta["okres_roczny"]
    )


    etykieta = (

        f"{rok} M{miesiac}"

        if miesiac

        else str(rok)
    )


    aktualizacja = (

        meta["serie"][
            3
            if miesiac
            else 1
        ].get(
            "aktualizacja-ostatnia",
            ""
        )
    )


    plik = (

        cache
        / f"{rok}_{okres}.json"
    )


    sygnatura = [
        1,
        API,
        ID_ZMIENNEJ,
        ID_PRZEKROJU,
        sorted(KODY_CN),
        ROZMIAR_STRONY,
        aktualizacja,
    ]


    stan = {

        "sygnatura":
            sygnatura,

        "rok":
            rok,

        "miesiac":
            miesiac,

        "id_okres":
            okres,

        "okres":
            etykieta,

        "nastepna_strona":
            0,

        "liczba_stron":
            0,

        "ostatnia_strona_api":
            None,

        "wiersze_api":
            0,

        "dane":
            [],

        "gotowe":
            False,
    }


    if (
        plik.exists()
        and
        not odswiez
    ):

        zapisany = json.loads(
            plik.read_text(
                encoding="utf-8"
            )
        )

        if (
            zapisany.get(
                "sygnatura"
            )
            == sygnatura
        ):

            stan = zapisany


    if stan["gotowe"]:

        print(
            f"{etykieta}: z cache.",
            flush=True
        )

        return stan


    wymiar_cn = (

        next(
            iter(
                meta["towary"].values()
            )
        )[
            "id-wymiar"
        ]
    )


    wymiar_kraju = (

        next(
            iter(
                meta["kraje"].values()
            )
        )[
            "id-wymiar"
        ]
    )


    wymiar_polski = 2


    while not stan["gotowe"]:

        strona = (
            stan["nastepna_strona"]
        )


        parametry = {
            "id-zmienna": ID_ZMIENNEJ,
            "id-przekroj": ID_PRZEKROJU,
            "id-rok": rok,
            "id-okres": okres,
            "ile-na-stronie": ROZMIAR_STRONY,
            "numer-strony": strona,
        }


        d = klient.pobierz(
            "variable/variable-data-section",
            parametry,
            dopuszczaj_404=(
                strona == 0
            )
        )


        if d is None:

            print(
                f"{etykieta}: "
                f"brak danych w API.",
                flush=True
            )

            return None


        if (
            not isinstance(
                d,
                dict
            )
            or
            not isinstance(
                d.get("data"),
                list
            )
        ):

            raise RuntimeError(
                f"Błędna odpowiedź API "
                f"dla {etykieta}."
            )


        dane = d["data"]


        if (
            not dane
            and
            strona == 0
            and
            d.get(
                "page-count",
                0
            )
            == 0
        ):

            return None


        ostatnia_strona = (
            d["page-count"]
        )


        if (
            stan[
                "ostatnia_strona_api"
            ]
            not in (
                None,
                ostatnia_strona
            )
        ):

            raise RuntimeError(
                f"GUS zmienił liczbę stron "
                f"w trakcie pobierania "
                f"{etykieta}."
            )


        for w in dane:

            if (
                w.get(
                    "id-zmienna"
                )
                != ID_ZMIENNEJ
            ):

                raise RuntimeError(
                    "API zwróciło dane "
                    "innej zmiennej."
                )


            if (
                w.get(
                    "id-przekroj"
                )
                != ID_PRZEKROJU
            ):

                raise RuntimeError(
                    "API zwróciło dane "
                    "innego przekroju."
                )


            cn = pozycja_wymiaru(
                w,
                wymiar_cn
            )


            if cn not in meta["towary"]:

                continue


            kraj = pozycja_wymiaru(
                w,
                wymiar_kraju
            )


            if kraj not in meta["kraje"]:

                continue


            # sprawdzenie Polski
            if (
                pozycja_wymiaru(
                    w,
                    wymiar_polski
                )
                != 33617
            ):

                raise RuntimeError(
                    "Dane nie dotyczą Polski."
                )


            stan[
                "dane"
            ].append(
                {
                    **w,
                    "kraj_id": kraj,
                    "cn_id": cn,
                }
            )


        stan.update(

            nastepna_strona=(
                strona + 1
            ),

            liczba_stron=(
                strona + 1
            ),

            ostatnia_strona_api=(
                ostatnia_strona
            ),

            wiersze_api=(
                stan["wiersze_api"]
                + len(dane)
            ),

            gotowe=(
                strona
                == ostatnia_strona
            ),

            data_pobrania=(
                datetime.now(
                    timezone.utc
                ).isoformat(
                    timespec="seconds"
                )
            ),
        )


        zapisz_json(
            plik,
            stan
        )


        print(
            f"{etykieta}: "
            f"strona "
            f"{strona + 1}/"
            f"{ostatnia_strona + 1}, "
            f"rekordów gazu: "
            f"{len(stan['dane'])}.",
            flush=True
        )


    return stan


# ============================================================
# AKTYWNOŚĆ KRAJU / CN
# ============================================================

def aktywna(
    pozycja,
    rok,
    miesiac
):

    poczatek = date(
        rok,
        miesiac or 1,
        1
    ).isoformat()


    koniec = date(

        rok,

        miesiac or 12,

        calendar.monthrange(
            rok,
            miesiac or 12
        )[1]

    ).isoformat()


    return (

        (
            pozycja.get(
                "data-poczatku"
            )
            or "0001-01-01"
        )[:10]
        <= koniec

        and

        (
            pozycja.get(
                "data-konca"
            )
            or "9999-12-31"
        )[:10]
        >= poczatek
    )


# ============================================================
# SŁOWNIKI
# ============================================================

def opis(
    slownik,
    ident
):

    if ident is None:

        return ""


    return (
        slownik
        .get(
            ident,
            {}
        )
        .get(
            "nazwa",
            ""
        )
    )


# ============================================================
# GENEROWANIE CSV
# ============================================================

def wiersze_csv(
    meta,
    stany,
    roczne
):

    kraje = sorted(

        meta[
            "kraje"
        ].items(),

        key=lambda kv:
            kv[1][
                "nazwa-pozycja"
            ]
    )


    towary = sorted(

        meta[
            "towary"
        ].items(),

        key=lambda kv:
            kv[1][
                "symbol"
            ]
    )


    for stan in stany:


        if (
            (
                stan["miesiac"]
                == 0
            )
            != roczne
        ):

            continue


        rekordy = {}


        for w in stan["dane"]:

            klucz = (
                w["kraj_id"],
                w["cn_id"]
            )


            if klucz in rekordy:

                raise RuntimeError(
                    f"Duplikat kraj/CN "
                    f"dla {stan['okres']}: "
                    f"{klucz}"
                )


            rekordy[
                klucz
            ] = w


        for (
            kraj_id,
            kraj
        ) in kraje:


            if not aktywna(
                kraj,
                stan["rok"],
                stan["miesiac"]
            ):

                continue


            for (
                cn_id,
                cn
            ) in towary:


                if not aktywna(
                    cn,
                    stan["rok"],
                    stan["miesiac"]
                ):

                    continue


                w = rekordy.get(
                    (
                        kraj_id,
                        cn_id
                    ),
                    {}
                )


                wartosc = w.get(
                    "wartosc"
                )


                if wartosc is not None:

                    status = (
                        "wartosc_opublikowana"
                    )


                elif not w:

                    status = (
                        "brak_rekordu_w_api"
                    )


                elif (
                    meta[
                        "tajnosc"
                    ]
                    .get(
                        w.get(
                            "id-tajnosci"
                        ),
                        {}
                    )
                    .get(
                        "oznaczenie"
                    )
                    == "(:)"
                ):

                    status = (
                        "tajemnica_statystyczna"
                    )

                    wartosc = None


                elif (
                    w.get(
                        "id-brak-wartosci"
                    )
                    == 42
                ):

                    status = (
                        "zjawisko_nie_wystapilo"
                    )


                else:

                    status = (
                        "brak_wartosci_w_api"
                    )


                yield {

                    "okres":
                        stan["okres"],

                    "rok":
                        stan["rok"],

                    "miesiac":
                        (
                            stan["miesiac"]
                            or ""
                        ),

                    "kraj":
                        kraj[
                            "nazwa-pozycja"
                        ],

                    "kod_kraju":
                        kraj.get(
                            "symbol",
                            ""
                        ),

                    "typ_kraju":
                        (
                            "kategoria_specjalna"

                            if (
                                kraj.get(
                                    "symbol"
                                )
                                in
                                KATEGORIE_SPECJALNE
                            )

                            else

                            "kraj_lub_terytorium"
                        ),

                    "kod_cn":
                        cn[
                            "symbol"
                        ],

                    "towar":
                        cn[
                            "nazwa-pozycja"
                        ],

                    "wartosc":
                        (
                            ""

                            if wartosc is None

                            else

                            format(
                                Decimal(
                                    str(
                                        wartosc
                                    )
                                ),
                                "f"
                            ).replace(
                                ".",
                                ","
                            )
                        ),

                    "jednostka":
                        cn.get(
                            "jednostka",
                            ""
                        ),

                    "status":
                        status,

                    "flaga_gus":
                        opis(
                            meta["flagi"],
                            w.get(
                                "id-flaga"
                            )
                        ),

                    "tajnosc_gus":
                        opis(
                            meta["tajnosc"],
                            w.get(
                                "id-tajnosci"
                            )
                        ),

                    "brak_wartosci_gus":
                        opis(
                            meta["braki"],
                            w.get(
                                "id-brak-wartosci"
                            )
                        ),

                    "wartosc_opisowa_gus":
                        w.get(
                            "wartosc-opisowa",
                            ""
                        ),

                    "precyzja_gus":
                        w.get(
                            "precyzja",
                            ""
                        ),

                    "id_kraju_gus":
                        kraj_id,

                    "id_cn_gus":
                        cn_id,

                    "id_okresu_gus":
                        stan[
                            "id_okres"
                        ],
                }


# ============================================================
# EKSPORT PLIKÓW
# ============================================================

def eksportuj(
    folder,
    meta,
    stany,
    raport
):

    zapisz_csv(

        folder
        / "gus_gaz_export_miesieczne.csv",

        KOLUMNY,

        wiersze_csv(
            meta,
            stany,
            False
        )
    )


    zapisz_csv(

        folder
        / "gus_gaz_export_roczne.csv",

        KOLUMNY,

        wiersze_csv(
            meta,
            stany,
            True
        )
    )


    zapisz_csv(

        folder
        / "raport_pobierania_export.csv",

        [
            "okres",
            "typ_okresu",
            "status",
            "strony",
            "rekordy_gazu",
            "uwagi",
        ],

        raport
    )


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description=__doc__
    )


    parser.add_argument(
        "--od",
        type=int,
        default=ROK_OD,
        dest="rok_od"
    )


    parser.add_argument(
        "--do",
        type=int,
        default=(
            ROK_DO
            or date.today().year
        ),
        dest="rok_do"
    )


    # ========================================================
    # KATALOG - DZIAŁA W .PY I JUPYTERZE
    # ========================================================

    if "__file__" in globals():

        katalog_bazowy = (
            Path(
                __file__
            )
            .resolve()
            .parent
        )

    else:

        katalog_bazowy = (
            Path.cwd()
        )


    parser.add_argument(

        "--katalog",

        type=Path,

        default=(
            katalog_bazowy
            / "gus_gaz_export"
        )
    )


    parser.add_argument(
        "--odswiez",
        action="store_true"
    )


    # ========================================================
    # JUPYTER
    # ========================================================

    if "ipykernel" in sys.modules:

        args, _ = (
            parser.parse_known_args()
        )

    else:

        args = (
            parser.parse_args()
        )


    # ========================================================
    # WALIDACJA
    # ========================================================

    if not (
        2004
        <= args.rok_od
        <= args.rok_do
        <= date.today().year
    ):

        parser.error(
            "Nieprawidłowy zakres lat."
        )


    # ========================================================
    # API
    # ========================================================

    klient = KlientGUS(
        KLUCZ_API
    )


    folder = (
        args.katalog
        .resolve()
    )


    cache = (
        folder
        / "cache"
    )


    cache.mkdir(
        parents=True,
        exist_ok=True
    )


    print(
        "Pobieram metadane GUS...",
        flush=True
    )


    meta = wczytaj_metadane(
        klient
    )


    zapisz_json(

        folder
        / "metadane_gus_export.json",

        meta
    )


    print(
        "\nEksport gazu z Polski."
    )


    print(
        "Pobierane kody:"
    )


    for cn in sorted(

        meta[
            "towary"
        ].values(),

        key=lambda x:
            x["symbol"]
    ):

        print(
            f"  "
            f"{cn['symbol']} - "
            f"{cn['nazwa-pozycja']}"
        )


    # ========================================================
    # LISTA OKRESÓW
    # ========================================================

    stany = []
    raport = []

    dzis = date.today()

    zadania = []


    for rok in range(
        args.rok_od,
        args.rok_do + 1
    ):


        for miesiac in (
            list(
                range(
                    1,
                    13
                )
            )
            + [0]
        ):


            # pomijamy bieżący miesiąc
            # i bieżący rok roczny

            if (
                rok
                == dzis.year

                and (
                    miesiac == 0
                    or
                    miesiac >= dzis.month
                )
            ):

                continue


            seria = (

                meta[
                    "serie"
                ][
                    3
                    if miesiac
                    else 1
                ]
            )


            lata = [

                int(x)

                for x
                in re.findall(
                    r"\d{4}",
                    str(
                        seria.get(
                            "szereg-czasowy",
                            ""
                        )
                    )
                )
            ]


            if not lata:

                raise RuntimeError(
                    "Nie udało się rozpoznać "
                    "zakresu lat."
                )


            if (
                min(lata)
                <= rok
                <= max(lata)
            ):

                zadania.append(
                    (
                        rok,
                        miesiac
                    )
                )


    # ========================================================
    # POBIERANIE
    # ========================================================

    try:

        for (
            indeks,
            (
                rok,
                miesiac
            )
        ) in enumerate(
            zadania
        ):


            wpis = {

                "okres":
                    (
                        f"{rok} M{miesiac}"
                        if miesiac
                        else str(rok)
                    ),

                "typ_okresu":
                    (
                        "miesieczny"
                        if miesiac
                        else "roczny"
                    ),

                "status":
                    "w_trakcie",

                "strony":
                    "",

                "rekordy_gazu":
                    "",

                "uwagi":
                    "",
            }


            raport.append(
                wpis
            )


            try:

                stan = pobierz_okres(

                    klient,
                    meta,
                    rok,
                    miesiac,
                    cache,
                    args.odswiez
                )


            except (
                RuntimeError,
                OSError,
                ValueError,
                KeyboardInterrupt
            ):

                wpis[
                    "status"
                ] = (
                    "przerwano_lub_blad"
                )

                wpis[
                    "uwagi"
                ] = (
                    "Okres niekompletny. "
                    "Uruchom ponownie."
                )

                raise


            if stan is None:

                wpis[
                    "status"
                ] = (
                    "brak_okresu_w_api"
                )

                wpis[
                    "uwagi"
                ] = (
                    "Brak danych w API. "
                    "Nie oznacza to "
                    "zerowego eksportu."
                )


            else:

                stany.append(
                    stan
                )


                wpis.update(

                    status="pobrano",

                    strony=(
                        stan[
                            "liczba_stron"
                        ]
                    ),

                    rekordy_gazu=(
                        len(
                            stan[
                                "dane"
                            ]
                        )
                    )
                )


            # zapis po każdym roku
            if (
                indeks
                == len(
                    zadania
                )
                - 1

                or (
                    zadania[
                        indeks + 1
                    ][0]
                    != rok
                )
            ):

                eksportuj(
                    folder,
                    meta,
                    stany,
                    raport
                )


    finally:

        if raport:

            eksportuj(
                folder,
                meta,
                stany,
                raport
            )


    # ========================================================
    # PODSUMOWANIE
    # ========================================================

    if not stany:

        raise RuntimeError(
            "Nie pobrano żadnego "
            "kompletnego okresu."
        )


    print(
        "\n=========================="
    )

    print(
        "GOTOWE"
    )

    print(
        "=========================="
    )


    print(
        f"Kompletnych okresów: "
        f"{len(stany)}"
    )


    print(
        f"Folder: {folder}"
    )


    print(
        "\nPliki:"
    )

    print(
        "gus_gaz_export_miesieczne.csv"
    )

    print(
        "gus_gaz_export_roczne.csv"
    )

    print(
        "raport_pobierania_export.csv"
    )

    print(
        "metadane_gus_export.json"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        main()


    except KeyboardInterrupt:

        print(
            "\nPrzerwano. "
            "Uruchom ponownie, "
            "aby kontynuować.",
            file=sys.stderr
        )


    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError
    ) as exc:

        print(
            f"\nBŁĄD: {exc}",
            file=sys.stderr
        )

        raise
