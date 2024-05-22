from typing import List, Tuple, TypedDict, Union

import h5py
import hdf5plugin
import numcodecs.registry as registry
import numpy as np
from numcodecs.abc import Codec
from numcodecs.fixedscaleoffset import FixedScaleOffset
from pydantic import BaseModel, validator
from xarray.coding.variables import _choose_float_dtype

_non_standard_filters = {"gzip": "zlib"}


class BloscProperties(BaseModel):
    blocksize: int
    clevel: int
    shuffle: int
    cname: str

    @validator("cname", pre=True)
    def get_cname_from_code(cls, v):
        blosc_compressor_codes = {
            value: key
            for key, value in hdf5plugin._filters.Blosc._Blosc__COMPRESSIONS.items()
        }
        return blosc_compressor_codes[v]


class CFCodec(TypedDict):
    target_dtype: np.dtype
    codec: Codec


def _filter_to_codec(
    filter_id: str, filter_properties: Union[int, None, Tuple] = None
) -> Codec:
    id_int = None
    id_str = None
    try:
        id_int = int(filter_id)
    except ValueError:
        id_str = filter_id

    if id_str:
        if id_str in _non_standard_filters.keys():
            id = _non_standard_filters[id_str]
        else:
            id = id_str
        conf = {"id": id}
        if id == "zlib":
            conf["level"] = filter_properties  # type: ignore[assignment]
    if id_int:
        filter = hdf5plugin.get_filters(id_int)[0]
        id = filter.filter_name
        if id == "blosc" and isinstance(filter_properties, tuple):
            blosc_props = BloscProperties(
                **{
                    k: v
                    for k, v in zip(
                        BloscProperties.__fields__.keys(), filter_properties[-4:]
                    )
                }
            )
            conf = blosc_props.model_dump()  # type: ignore[assignment]
            conf["id"] = id

    codec = registry.get_codec(conf)
    return codec


def cfcodec_from_dataset(dataset: h5py.Dataset) -> Codec | None:
    attributes = {attr: dataset.attrs[attr] for attr in dataset.attrs}
    mapping = {}
    if "scale_factor" in attributes:
        mapping["scale_factor"] = 1 / attributes["scale_factor"][0]
    else:
        mapping["scale_factor"] = 1
    if "add_offset" in attributes:
        mapping["add_offset"] = attributes["add_offset"]
    else:
        mapping["add_offset"] = 0
    if mapping["scale_factor"] != 1 or mapping["add_offset"] != 0:
        float_dtype = _choose_float_dtype(dtype=dataset.dtype, mapping=mapping)
        target_dtype = np.dtype(float_dtype)
        codec = FixedScaleOffset(
            offset=mapping["add_offset"],
            scale=mapping["scale_factor"],
            dtype=target_dtype,
            astype=dataset.dtype,
        )
        cfcodec = CFCodec(target_dtype=target_dtype, codec=codec)
        return cfcodec
    else:
        return None


def codecs_from_dataset(dataset: h5py.Dataset) -> List[Codec]:
    codecs = []
    for filter_id, filter_properties in dataset._filters.items():
        codec = _filter_to_codec(filter_id, filter_properties)
        codecs.append(codec)
    return codecs
