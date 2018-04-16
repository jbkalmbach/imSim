"""
Code to determine deviations in the Zernike coefficients determined by the
LSST AOS closed loop control system. Results do not include contributions from
the open loop lookup table.
"""

import os

from astropy.io import fits
import numpy as np
from scipy.interpolate import interp2d
from timeit import timeit

from galsim.zernike import Zernike, zernikeBasis
import lsst.utils

FILE_DIR = lsst.utils.getPackageDir('imsim')

AOS_PATH = os.path.join(FILE_DIR, 'data', 'optics_data', 'aos_sim_results.txt')
MATRIX_PATH = os.path.join(FILE_DIR, 'data', 'optics_data', 'sensitivity_matrix.txt')
NOMINAL_PATH = os.path.join(FILE_DIR, 'data', 'optics_data', 'annular_nominal_coeff.txt')
ZEMAX_PATH = os.path.join(FILE_DIR, 'data', 'optics_data', 'annular_zemax_estimates.fits')


def cartesian_coords():
    """
    Return 35 cartesian sampling coordinates in the LSST field of view

    These sampling coordinates correspond to the position sampling of the
    sensitivity matrix found in MATRIX_PATH

    @param [out] an array of 35 x coordinates in degrees

    @param [out] an array of 35 y coordinates in degrees
    """

    # Initialize with central point
    x_list = [0.]
    y_list = [0.]

    # Loop over points on spines
    radii = [0.379, 0.841, 1.237, 1.535, 1.708]
    angles = np.deg2rad([0, 60, 120, 180, 240, 300])
    for radius in radii:
        for angle in angles:
            x_list.append(radius * np.cos(angle))
            y_list.append(radius * np.sin(angle))

    # Add Corner raft points by hand
    x_list.extend([1.185, -1.185, -1.185, 1.185])
    y_list.extend([1.185, 1.185, -1.185, -1.185])

    return np.array(x_list), np.array(y_list)


def polar_coords():
    """
    Return 35 polar sampling coordinates in the LSST field of view.

    These sampling coordinates correspond to the position sampling of the
    sensitivity matrix found in MATRIX_PATH

    @param [out] an array of 35 r coordinates in degrees

    @param [out] an array of 35 theta coordinates in radians
    """

    # Initialize with central point
    r_list = [0.]
    theta_list = [0.]

    # Loop over points on spines
    radii = [0.379, 0.841, 1.237, 1.535, 1.708]
    angles = [0, 60, 120, 180, 240, 300]
    for radius in radii:
        for angle in angles:
            r_list.append(radius)
            theta_list.append(np.deg2rad(angle))

    # Add Corner raft points
    x_raft_coords = [1.185, -1.185, -1.185, 1.185]
    y_raft_coords = [1.185, 1.185, -1.185, -1.185]
    for x, y in zip(x_raft_coords, y_raft_coords):
        theta_list.append(np.arctan2(y, x))
        r_list.append(np.sqrt(x * x + y * y))

    return np.array(r_list), np.array(theta_list)


def _interp_nominal_coeff(zemax_est, fp_x, fp_y):
    """
    Interpolates the nominal annular Zernike coefficients for given coordinates

    @param [in] fp_x is an x coordinate in the LSST field of view

    @param [in] fp_y is an x coordinate in the LSST field of view

    @param [out] An array of 19 zernike coefficients for z=4 through z=22
    """

    # Determine x and y coordinates of zemax_est
    n_samples = 32  # grid size
    fov = [-2.0, 2.0, -2.0, 2.0]  # [x_min, x_max, y_min, y_max]
    x_sampling = np.arange(fov[0], fov[1], (fov[1] - fov[0]) / n_samples)
    y_sampling = np.arange(fov[2], fov[3], (fov[3] - fov[2]) / n_samples)

    max_fov = 1.75
    if abs(fp_x) > max_fov or abs(fp_y) > max_fov:
        raise ValueError('Given coordinates are outside the field of view.')

    num_zernike_coeff = zemax_est.shape[2]
    out_arr = np.zeros(num_zernike_coeff)
    for i in range(num_zernike_coeff):
        interp_func = interp2d(x_sampling, y_sampling,
                               zemax_est[:, :, i],
                               kind='linear')

        out_arr[i] = interp_func(fp_x, fp_y)[0]

    return out_arr[3:] # Remove first four Zernike coefficients


def _gen_nominal_coeff(zemax_path=ZEMAX_PATH):
    """
    Use zemax estimates to determine the nominal coeff at 35 sampling coordinates

    Results are written to NOMINAL_PATH. By default we use tabulated zemax
    estimates found at ZEMAX_PATH.

    @param [in] zemax_path is the path of a fits files containing zemax
        estimates for the nominal Zernike coefficients
    """

    # Nominal coefficients of annular Zernikes generated by zemax
    zemax_est = fits.open(zemax_path)[0].data
    assert zemax_est.shape == (32, 32, 22)

    x_coords, y_coords = cartesian_coords()
    num_cords = len(x_coords)

    out_array = np.zeros((num_cords, 19))
    for i in range(num_cords):
        out_array[i] = _interp_nominal_coeff(zemax_est, x_coords[i], y_coords[i])

    # Required output is (19, 35)
    out_array_t = out_array.transpose()
    np.savetxt(NOMINAL_PATH, out_array_t)


def mock_deviations(seed=None):
    """
    Returns an array of random mock optical deviations as a shape (50,) array.

    Generates a set of random deviations in each optical degree of freedom for
    LSST. Ech degree of freedom has a seperate, normal distribution. Parameters
    used to create each distribution calculated based simulations of the
    adaptive optics system found in AOS_PATH.

    @param [out] A shape (50,) array representing mock optical distortions
    """

    aos_sim_results = np.genfromtxt(AOS_PATH, skip_header=1)
    assert aos_sim_results.shape[0] == 50

    np.random.seed(seed)
    avg = np.average(aos_sim_results, axis=1)
    std = np.std(aos_sim_results, axis=1)
    return np.random.normal(avg, std)


def test_runtime(n_runs, n_coords, verbose=False):
    """
    Determines average run times to both instantiate the OpticalZernikes class
    and to evaluate the cartesian_coeff method.

    @param [in] n_runs is the total number of runs to average runtimes over

    @param [in] n_coords is the total number of cartesian coordinates
        to average runtimes over

    @param [in] verbose is a boolean specifying whether to print results
        (default = false)

    @param [out] The average initialization time in seconds

    @param [out] The average evaluation time of cartesian_coeff in seconds
    """

    init_time = timeit('OpticalZernikes()', globals=globals(), number=n_runs)

    optical_state = OpticalZernikes()
    x_coords = np.random.uniform(-1.5, 1.5, size=(n_coords,))
    y_coords = np.random.uniform(-1.5, 1.5, size=(n_coords,))
    runtime = timeit('optical_state.cartesian_coeff(x_coords, y_coords)',
                     globals=locals(), number=n_runs)

    if verbose:
        print('Averages over {} runs:'.format(n_runs))
        print('Init time (s):', init_time / n_runs)
        print('Run time for', n_coords, 'cartesian coords (s):',
              runtime / n_runs)

    return


class OpticalZernikes:
    """
    Instances of this class can be thought of as fixed, independent states of
    the LSST optics system. This class provides functions for estimating the
    residual zernike coefficients left uncorrected by the LSST AOS closed loop
    control system. For a given location in the focal plane, this class provides
    coefficients for 19 zernike polynomials ranging from zernike 4 through
    zernike 22 (in the NOLL indexing scheme)
    """

    sensitivity = np.genfromtxt(MATRIX_PATH).reshape((35, 19, 50))
    nominal_coeff = np.genfromtxt(NOMINAL_PATH)
    cartesian_coords = cartesian_coords()
    _polar_coords = None

    def __init__(self, deviations=None):
        """
        @param [in] deviations is a (35, 50) array representing deviations in
        each of LSST's optical degrees of freedom at 35 sampling coordinates
        """

        if deviations is None:
            self.deviations = mock_deviations()

        else:
            self.deviations = deviations

        self.deviation_coeff = np.dot(self.sensitivity, self.deviations).transpose()
        self.sampling_coeff = np.add(self.deviation_coeff, self.nominal_coeff)
        self._fit_functions = self._optimize_fits()

    def _optimize_fits(self):
        """
        Generate a separate fit function for each zernike coefficient

        @param [out] A list of 19 functions
        """

        x, y = self.cartesian_coords
        basis = zernikeBasis(22, x, y)

        out = []
        for coefficient in self.sampling_coeff:
            coefs, _, _, _ = np.linalg.lstsq(basis.T, coefficient, rcond=None)
            optimized_func = Zernike(coefs).evalCartesian
            out.append(optimized_func)

        return out

    def _interp_deviations(self, fp_x, fp_y, kind='cubic'):
        """
        Determine the zernike coefficients at given coordinates by interpolating

        @param [in] fp_x is the desired x coordinate

        @param [in] fp_y is the desired y coordinate

        @param [in] kind is the type of interpolation to perform. (eg. "linear")

        @param [out] An array of 19 zernike coefficients for z=4 through z=22
        """

        x, y = self.cartesian_coords
        num_zernike_coeff = self.sampling_coeff.shape[0]
        out_arr = np.zeros(num_zernike_coeff)

        for i, coeff in enumerate(self.sampling_coeff):
            interp_func = interp2d(x, y, coeff, kind=kind)
            out_arr[i] = interp_func(fp_x, fp_y)[0]

        return out_arr

    @property
    def polar_coords(self):
        """
        Lazy loads 35 polar sampling coordinates in the LSST field of view

        @param [out] an array of 35 r coordinates

        @param [out] an array of 35 theta coordinates
        """

        if self._polar_coords is None:
            self._polar_coords = polar_coords()

        return self._polar_coords

    def polar_coeff(self, fp_r, fp_t):
        """
        Returns the zernike coefficients at a given location in the field of view

        @param [in] fp_r is a radial coordinate or an array of radial coordinates

        @param [in] fp_t is an angular coordinate or an array of angular coordinates

        @param [out] An array of 19 zernike coefficients for z=4 through z=22
        """

        x = fp_r * np.cos(fp_t)
        y = fp_r * np.sin(fp_t)
        return np.array([func(x, y) for func in self._fit_functions])

    def cartesian_coeff(self, fp_x, fp_y):
        """
        Returns the zernike coefficients at a given location in the field of view

        @param [in] fp_x is an x coordinate an array of x coordinates

        @param [in] fp_y is a y coordinate an array of y coordinates

        @param [out] An array of 19 zernike coefficients for z=4 through z=22
        """

        return np.array([func(fp_x, fp_y) for func in self._fit_functions])
