"""
This module contains functions to create NIRISS reference files.


Grism
-----
### February 28, 2017

* Throughput curves generated using the "current" ETC configuration files
found at `/grp/jwst/wit/niriss/ETC/CURRENT/`. These include the following
components:

        - Telescope OTE (primary area = 254009.0 cm^2 , from pandeia/ETC)
        - Internal instrument optics
        - Blocking filter passband
        - Disperser passband (`p1=A`, `p2=C`, `p3=D` and `m1=E` orders/"beams").
        - Detector QE and quantum yield

        Through there are separate ETC files, these are currently the same for
        both the GR150C and GR150R dispersing elements.  (**NB:** higher orders
        are visible in the CV3 test data but are not currently included in the
        configuration files.)

* The ETC doesn't have a 0th order throughput file (`beam=B`), so for now is
  taken to be the original file generated by V. Dixon for the "WFSS cookbook".

* The trace configuration has been updated by G. Brammer from the original
  cookbook files based on the CV trace and wavelength calibration data.

        - Separate traces for the two dispersers GR150R (column) and GR150C (row)
dispersing elements.

        - It is now assumed that the traces for all orders and filters are
        described by a common polynomial function, though with a 0th order
        offset for the different blocking filters.  This simplifies the
        analysis and is also fully consistent with the CV3 data.

        - **The configuration files are generated in the old aXe convention
        with increasing wavelength of the p1st order spectra increasing towards
         `+x` detector pixels**.  This corresponds to CCW rotations of the
          DMS-format images of 90 (GR150R) and 180 (GR150C) degrees.

        - The traces are defined relative to a reference position of the
         filter wheel specified in the `FWCPOS_REF` parameter (in degrees).
           For a given science observation, the trace polynomials must be
            rotated by an angle `FWCPOS - FWCPOS_REF`.  This rotation has an
             analytic solution for linear and quadratic traces.

        - Spatial variation of the trace polynomials was determined from 5
         separate locations on the detector.

"""
import re
import datetime
import numpy as np
from asdf.tags.core import Software, HistoryEntry
from astropy.modeling.models import Polynomial2D, Polynomial1D
from astropy.io import fits
from astropy import units as u

from jwst.datamodels import NIRISSGrismModel
from jwst.datamodels import wcs_ref_models


def common_reference_file_keywords(reftype=None,
                                   author="STScI",
                                   exp_type=None,
                                   description="NIRISS Reference File",
                                   title="NIRISS Reference File",
                                   useafter="2014-01-01T00:00:00",
                                   filtername=None,
                                   filename="",
                                   pupil=None, **kwargs):
    """
    exp_type can be also "N/A", or "ANY".
    """
    if exp_type is None:
        raise ValueError("Expected exp_type")
    if reftype is None:
        raise ValueError("Expected reftype")

    ref_file_common_keywords = {
        "author": author,
        "description": description,
        "exposure": {"type": exp_type},
        "instrument": {"name": "NIRISS",
                       "detector": "NIS"},
        "pedigree": "ground",
        "reftype": reftype,
        "telescope": "JWST",
        "title": title,
        "useafter": useafter,
        "filename": filename,
        }

    if filtername is not None:
        ref_file_common_keywords["instrument"]["filter"] = filtername
    if pupil is not None:
        ref_file_common_keywords["instrument"]["pupil"] = pupil

    ref_file_common_keywords.update(kwargs)
    return ref_file_common_keywords



def read_sensitivity_file(filename):
    """Read the sensativity fits file.

    This is assumed to be an MEF file with a table in the first extension

    Parameters
    ----------
    filename : str
        The name of the fits file

    Returns
    -------
    A dictionary of lists where the keys are the column names
    """
    if not isinstance(filename, str):
        raise ValueError("Expected the name of the sensitivity fits file")

    sens = dict()
    with fits.open(filename) as fh:
        columns = fh[1].header['TTYPE*']
        for c in columns.values():
            sens[c] = fh[1].data.field(c)
            if "WAVE" in c:
                sens["WRANGE"] = [np.min(sens[c]), np.max(sens[c])]
    return sens


def split_order_info(keydict):
    """Accumulate keys just for each Beam/order.

    Designed to take as input the dictionary created by dict_from_file
    split out and accumulate the keys for each beam/order.
    The keys must have the beam in their string, the spurious beam designation
    is removed from the returned dictionary. Keywords with the same first name
    in the underscore separated string followed by a number are assumed to be
    ranges


    Parameters
    ----------
    keydict : dictionary
        Dictionary of key value pairs

    Returns
    -------
    dictionary of beams, where each beam has a dictionary of key-value pairs
    Any key pairs which are not associated with a beam get a separate entry
    """

    if not isinstance(keydict, dict):
        raise ValueError("Expected an input dictionary")

    # has beam name fits token
    # token = re.compile('^[a-zA-Z]*_(?:[+\-]){0,1}[a-zA-Z0-9]{1}_{1}')
    token = re.compile('^[a-zA-Z]*_[a-zA-Z0-9]{1}_(?:\w)')
    rangekey = re.compile('^[a-zA-Z]*_[0-1]{1,1}$')
    rdict = dict()  # return dictionary
    beams = list()
    savekey = dict()

    # prefetch number of Beams, beam is the second string
    for key in keydict:
        if token.match(key):
            b = key.split("_")[1].upper()
            if b not in beams:
                beams.append(b)
                rdict[b] = dict()
            newkey = key.replace("_{}".format(b), "")
            rdict[b][newkey] = keydict[key]

    # look for range variables to make them into tuples
    for b, d in rdict.items():
        if isinstance(d, dict):
            keys = d.keys()
        else:
            keys = []
        rkeys = []
        odict = {}
        for k in keys:
            if rangekey.match(k):
                rkeys.append(k)
        for k in rkeys:
            mlist = [m for m in rkeys if k.split("_")[0] in m]
            root = mlist[0].split("_")[0]
            if root not in odict:
                for mk in mlist:
                    if eval(mk[-1]) == 0:
                        zero = d[mk]
                    elif eval(mk[-1]) == 1:
                        one = d[mk]
                    else:
                        raise ValueError("Unexpected range variable {}"
                                         .format(mk))
                odict[root] = (zero, one)
        # combine the dictionaries and remove the old keys
        if odict:
            d.update(odict)
        if rkeys:
            for k in rkeys:
                del d[k]

    return rdict


def dict_from_file(filename):
    """Read in a file and return a dict of the key value pairs.

    This is a generic read for a text file with the line following format:

    keyword<token>value

    Where keyword should start with a character, not a number
    Non-alphabetic starting characters are ignored
    <token> can be space or comma

    Parameters
    ----------
    filename : str
        Name of the file to interpret

    Examples
    --------
    dict_from_file('NIRISS_C.conf')

    Returns
    -------
    dictionary of deciphered keys and values

    """
    token = '\s+|(?<!\d)[,](?!\d)'
    letters = re.compile("(^[a-zA-Z])")  # starts with a letter
    numbers = re.compile("(^(?:[+\-])?(?:\d*)(?:\.)?(?:\d*)?(?:[eE][+\-]?\d*$)?)")
    empty = re.compile("(^\s*$)")  # is a blank line

    print("\nReading {0:s}  ...".format(filename))
    with open(filename, 'r') as fh:
        lines = fh.readlines()
    content = dict()
    for line in lines:
        value = None
        vallist = []
        key = None
        if not empty.match(line):
            if letters.match(line):
                pair = re.split(token, line.strip(), maxsplit=10)
                if len(pair) == 2:
                    key = pair[0]
                    if numbers.fullmatch(pair[1]):
                        value = eval(pair[1])
                else:  # more than 2 values
                    key = pair[0]
                    vals = pair[1:]
                    for v in vals:
                        if numbers.fullmatch(v):
                            vallist.append(eval(v))
                        else:
                            raise ValueError("Unexpected value for {0}"
                                             .format(key))

        if key:
            if (("FILTER" not in key) and ("SENS" not in key)):
                if (value is None):
                    content[key] = vallist
                    print("Setting {0:s} = {1}".format(key, vallist))
                else:
                    content[key] = value
                    print("Setting {0:s} = {1}".format(key, value))
    return content
