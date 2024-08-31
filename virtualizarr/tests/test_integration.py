import numpy as np
import pytest
import xarray as xr
import xarray.testing as xrt

from virtualizarr import open_virtual_dataset
from virtualizarr.manifests.array import ManifestArray
from virtualizarr.manifests.manifest import ChunkManifest
from virtualizarr.zarr import ZArray


@pytest.mark.parametrize(
    "inline_threshold, vars_to_inline",
    [
        (5e2, ["lat", "lon"]),
        (5e4, ["lat", "lon", "time"]),
        pytest.param(
            5e7,
            ["lat", "lon", "time", "air"],
            marks=pytest.mark.xfail(reason="scale factor encoding"),
        ),
    ],
)
def test_numpy_arrays_to_inlined_kerchunk_refs(
    netcdf4_file, inline_threshold, vars_to_inline
):
    from kerchunk.hdf import SingleHdf5ToZarr

    # inline_threshold is chosen to test inlining only the variables listed in vars_to_inline
    expected = SingleHdf5ToZarr(
        netcdf4_file, inline_threshold=int(inline_threshold)
    ).translate()

    # loading the variables should produce same result as inlining them using kerchunk
    vds = open_virtual_dataset(
        netcdf4_file, loadable_variables=vars_to_inline, indexes={}
    )
    refs = vds.virtualize.to_kerchunk(format="dict")
    # TODO I would just compare the entire dicts but kerchunk returns inconsistent results - see https://github.com/TomNicholas/VirtualiZarr/pull/73#issuecomment-2040931202
    # assert refs == expected
    assert refs["refs"]["air/0.0.0"] == expected["refs"]["air/0.0.0"]
    assert refs["refs"]["lon/0"] == expected["refs"]["lon/0"]
    assert refs["refs"]["lat/0"] == expected["refs"]["lat/0"]
    assert refs["refs"]["time/0"] == expected["refs"]["time/0"]


@pytest.mark.parametrize("format", ["dict", "json", "parquet"])
class TestKerchunkRoundtrip:
    def test_kerchunk_roundtrip_no_concat(self, netcdf4_file, tmpdir, format):
        ds = xr.open_dataset(netcdf4_file, decode_times=False)

        # use open_dataset_via_kerchunk to read it as references
        vds = open_virtual_dataset(netcdf4_file, indexes={})

        if format == "dict":
            # write those references to an in-memory kerchunk-formatted references dictionary
            ds_refs = vds.virtualize.to_kerchunk(format=format)

            # use fsspec to read the dataset from the kerchunk references dict
            roundtrip = xr.open_dataset(ds_refs, engine="kerchunk", decode_times=False)
        else:
            # write those references to disk as kerchunk references format
            vds.virtualize.to_kerchunk(f"{tmpdir}/refs.{format}", format=format)

            # use fsspec to read the dataset from disk via the kerchunk references
            roundtrip = xr.open_dataset(
                f"{tmpdir}/refs.{format}", engine="kerchunk", decode_times=False
            )

        # assert identical to original dataset
        xrt.assert_identical(roundtrip, ds)

    @pytest.mark.parametrize("decode_times,time_vars", [(False, []), (True, ["time"])])
    def test_kerchunk_roundtrip_concat(
        self, netcdf4_file, netcdf4_files, tmpdir, format, decode_times, time_vars
    ):
        netcdf1, netcdf2 = netcdf4_files

        # set up example xarray dataset
        ds = xr.open_dataset(netcdf4_file, decode_times=decode_times)

        # split into two datasets
        ds1 = xr.open_dataset(netcdf1, decode_times=decode_times)
        ds2 = xr.open_dataset(netcdf2, decode_times=decode_times)

        # save it to disk as netCDF (in temporary directory)
        ds1.to_netcdf(f"{tmpdir}/air1.nc")
        ds2.to_netcdf(f"{tmpdir}/air2.nc")

        # use open_dataset_via_kerchunk to read it as references
        vds1 = open_virtual_dataset(
            f"{tmpdir}/air1.nc",
            indexes={},
            loadable_variables=time_vars,
        )
        vds2 = open_virtual_dataset(
            f"{tmpdir}/air2.nc",
            indexes={},
            loadable_variables=time_vars,
        )

        if decode_times is False:
            assert vds1.time.dtype == np.dtype("float32")
        else:
            assert vds1.time.dtype == np.dtype("<M8[ns]")
            assert "units" in vds1.time.encoding
            assert "calendar" in vds1.time.encoding

        # concatenate virtually along time
        vds = xr.concat([vds1, vds2], dim="time", coords="minimal", compat="override")

        if format == "dict":
            # write those references to an in-memory kerchunk-formatted references dictionary
            ds_refs = vds.virtualize.to_kerchunk(format=format)

            # use fsspec to read the dataset from the kerchunk references dict
            roundtrip = xr.open_dataset(
                ds_refs, engine="kerchunk", decode_times=decode_times
            )
        else:
            # write those references to disk as kerchunk references format
            vds.virtualize.to_kerchunk(f"{tmpdir}/refs.{format}", format=format)

            # use fsspec to read the dataset from disk via the kerchunk references
            roundtrip = xr.open_dataset(
                f"{tmpdir}/refs.{format}", engine="kerchunk", decode_times=decode_times
            )
        if decode_times is False:
            # assert identical to original dataset
            xrt.assert_identical(roundtrip, ds)
        else:
            # they are very very close! But assert_allclose doesn't seem to work on datetimes
            assert (roundtrip.time - ds.time).sum() == 0
            assert roundtrip.time.dtype == ds.time.dtype
            assert roundtrip.time.encoding["units"] == ds.time.encoding["units"]
            assert roundtrip.time.encoding["calendar"] == ds.time.encoding["calendar"]

    def test_non_dimension_coordinates(self, tmpdir, format):
        # regression test for GH issue #105

        # set up example xarray dataset containing non-dimension coordinate variables
        ds = xr.Dataset(coords={"lat": (["x", "y"], np.arange(6.0).reshape(2, 3))})

        # save it to disk as netCDF (in temporary directory)
        ds.to_netcdf(f"{tmpdir}/non_dim_coords.nc")

        vds = open_virtual_dataset(f"{tmpdir}/non_dim_coords.nc", indexes={})

        assert "lat" in vds.coords
        assert "coordinates" not in vds.attrs

        if format == "dict":
            # write those references to an in-memory kerchunk-formatted references dictionary
            ds_refs = vds.virtualize.to_kerchunk(format=format)

            # use fsspec to read the dataset from the kerchunk references dict
            roundtrip = xr.open_dataset(ds_refs, engine="kerchunk", decode_times=False)
        else:
            # write those references to disk as kerchunk references format
            vds.virtualize.to_kerchunk(f"{tmpdir}/refs.{format}", format=format)

            # use fsspec to read the dataset from disk via the kerchunk references
            roundtrip = xr.open_dataset(
                f"{tmpdir}/refs.{format}", engine="kerchunk", decode_times=False
            )

        # assert equal to original dataset
        xrt.assert_identical(roundtrip, ds)

    def test_datetime64_dtype_fill_value(self, tmpdir, format):
        chunks_dict = {
            "0.0.0": {"path": "foo.nc", "offset": 100, "length": 100},
        }
        manifest = ChunkManifest(entries=chunks_dict)
        chunks = (1, 1, 1)
        shape = (1, 1, 1)
        zarray = ZArray(
            chunks=chunks,
            compressor={"id": "zlib", "level": 1},
            dtype=np.dtype("<M8[ns]"),
            # fill_value=0.0,
            filters=None,
            order="C",
            shape=shape,
            zarr_format=2,
        )
        marr1 = ManifestArray(zarray=zarray, chunkmanifest=manifest)
        ds = xr.Dataset(
            {
                "a": xr.DataArray(
                    marr1,
                    attrs={
                        "_FillValue": np.datetime64("1970-01-01T00:00:00.000000000")
                    },
                )
            }
        )

        if format == "dict":
            # write those references to an in-memory kerchunk-formatted references dictionary
            ds_refs = ds.virtualize.to_kerchunk(format=format)

            # use fsspec to read the dataset from the kerchunk references dict
            roundtrip = xr.open_dataset(ds_refs, engine="kerchunk")
        else:
            # write those references to disk as kerchunk references format
            ds.virtualize.to_kerchunk(f"{tmpdir}/refs.{format}", format=format)

            # use fsspec to read the dataset from disk via the kerchunk references
            roundtrip = xr.open_dataset(f"{tmpdir}/refs.{format}", engine="kerchunk")

        assert roundtrip.a.attrs == ds.a.attrs


def test_open_scalar_variable(tmpdir):
    # regression test for GH issue #100

    ds = xr.Dataset(data_vars={"a": 0})
    ds.to_netcdf(f"{tmpdir}/scalar.nc")

    vds = open_virtual_dataset(f"{tmpdir}/scalar.nc", indexes={})
    assert vds["a"].shape == ()
