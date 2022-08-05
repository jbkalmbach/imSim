"""
imSim module to make flats.
"""
import numpy as np
import galsim
from galsim.config import ImageBuilder, RegisterImageType

class LSST_FlatBuilder(ImageBuilder):
    """
    Creates a flat by successively adding lower level flat sky images.

    This is based on
    https://github.com/GalSim-developers/GalSim/blob/releases/2.0/devel/lsst/treering_flat.py

    Full LSST CCDs are assembled one amp at a time to limit the memory
    used by the galsim.SiliconSensor model.
    """


    def setup(self, config, base, image_num, obj_num, ignore, logger):
        """Do the initialization and setup for building the image.

        This figures out the size that the image will be, but doesn't actually build it yet.

        Parameters:
            config:     The configuration dict for the image field.
            base:       The base configuration dict.
            image_num:  The current image number.
            obj_num:    The first object number in the image.
            ignore:     A list of parameters that are allowed to be in config that we can
                        ignore here. i.e. it won't be an error if these parameters are present.
            logger:     If given, a logger object to log progress.

        Returns:
            xsize, ysize
        """
        logger.debug('image %d: Building Tiled: image, obj = %d,%d',image_num,image_num,obj_num)

        req = { 'counts_per_pixel' : float,
                'xsize' : int,
                'ysize': int
              }
        opt = { 'max_counts_per_iter': float,
                'buffer_size': int,
                'nx': int,
                'ny': int,
              }
        ignore = ignore + ['sed']
        params = galsim.config.GetAllParams(config, base, req=req, opt=opt, ignore=ignore)[0]

        self.counts_per_pixel = params['counts_per_pixel']
        self.xsize = params['xsize']
        self.ysize = params['ysize']
        self.buffer_size = params.get("buffer_size", 2)
        self.max_counts_per_iter = params.get("max_counts_per_iter", 1000)
        if 'sed' in config:
            self.sed = galsim.config.BuildSED(config, 'sed', base, logger=logger)[0]
            # When using an SED, we want smaller sections to avoid shooting billions of photons.
            # Still let the user specify, but increase the defaults.
            self.nx = params.get('nx', 16)
            self.ny = params.get('ny', 16)
        else:
            self.sed = None
            self.nx = params.get('nx', 8)
            self.ny = params.get('ny', 2)
        if 'sensor' in config and 'nrecalc' not in config['sensor']:
            config['sensor']['nrecalc'] = self.max_counts_per_iter * self.xsize * self.ysize
            logger.debug('setting nrecalc = %d',config['sensor']['nrecalc'])

        return self.xsize, self.ysize

    def buildImage(self, config, base, image_num, obj_num, logger):
        """
        Build the Image.

        Normally, this draws all the objects on the image, but for a flat there aren't any.
        So the returned image in this case is blank.

        Parameters:
            config:     The configuration dict for the image field.
            base:       The base configuration dict.
            image_num:  The current image number.
            obj_num:    The first object number in the image.
            logger:     If given, a logger object to log progress.

        Returns:
            a blank image and the current noise variance in the image (=0) as a tuple
        """
        image = galsim.ImageF(self.xsize, self.ysize, wcs=base['wcs'])
        base['image_type'] = 'FLAT'
        return image, 0

    def addNoise(self, image, config, base, image_num, obj_num, current_var, logger):
        """Add the "sky" and noise for the flat field image.

        Parameters:
            image:          The image onto which to add the noise.
            config:         The configuration dict for the image field.
            base:           The base configuration dict.
            image_num:      The current image number.
            obj_num:        The first object number in the image.
            current_var:    The current noise variance in each postage stamps.
            logger:         If given, a logger object to log progress.
        """
        # Get the sensor if there is one. (Or use a trivial one if not.)
        sensor = base.get('sensor', galsim.Sensor())

        # Update the rng to be appropriate for this image.
        rng = galsim.config.GetRNG(config, base, logger=logger, tag='LSST_Flat')
        sensor.updateRNG(rng)

        # Calculate how many iterations to do.
        niter = int(np.ceil(self.counts_per_pixel / self.max_counts_per_iter))
        counts_per_iter = self.counts_per_pixel / niter
        logger.info('Making flat: niter = %d, counts_per_iter = %d',niter,counts_per_iter)

        # Create a noise-free base image to add at each iteration.
        nrow, ncol = image.array.shape
        base_image = galsim.ImageF(image.bounds.withBorder(self.buffer_size), wcs=image.wcs)

        # Note: if using an SED, the accumulate step will apply tree rings, so don't do it here.
        if self.sed is None:
            # Start with flux in each pixel due to the variable pixel area from the WCS.
            base_image.wcs.makeSkyImage(base_image, sky_level=1.)

            # Rescale so that the mean sky level is counts_per_iter
            mean_pixel_area = base_image.array.mean()
            sky_level_per_iter = counts_per_iter/mean_pixel_area
            base_image *= sky_level_per_iter

        if self.sed is not None:
            if 'bandpass' not in base:
                raise RuntimeError('Using sed with flat builder requires a valid bandpass')
            wavelength_sampler = galsim.WavelengthSampler(self.sed, base['bandpass'])

        # Now we account for the sensor effects (i.e. brighter-fatter).
        # Build up the full CCD in sections to limit the memory required.
        dx = ncol//self.nx
        dy = nrow//self.ny
        for i in range(self.nx):
            xmin = i*dx + 1
            xmax = (i + 1)*dx
            # If nx doesn't divide ncol, make sure we cover the whole ccd by setting xmax=ncol
            if i == self.nx-1: xmax = ncol
            for j in range(self.ny):
                logger.info("section: %d, %d (of %d,%d)", i, j, self.nx, self.ny)
                ymin = j*dy + 1
                ymax = (j + 1)*dy
                if j == self.ny-1: ymax = nrow
                sec_bounds = galsim.BoundsI(xmin, xmax, ymin, ymax)
                bounds = sec_bounds.withBorder(self.buffer_size)
                # section is the image we are working on for this section.
                section = base_image[bounds].copy()
                # Start at 0.
                section.setZero()
                for it in range(niter):
                    logger.debug("iter %d", it)

                    if self.sed is None:
                        # Calculate the area of each pixel in the section image so far.
                        # This includes tree rings and BFE.
                        area = sensor.calculate_pixel_areas(section)

                        # Multiply the right part of the base image by the relative areas
                        # to get the right mean level and WCS effects.
                        temp = base_image[bounds].copy()
                        if isinstance(area, galsim.Image):
                            temp *= area / np.mean(area.array)

                        # What we have here is the expectation value in each pixel.
                        # We need to realize this according to Poisson statistics ith these means.
                        galsim.config.AddNoise(base, temp, 0., logger)

                        # Finally, add this to the image we are building up for this section.
                        section += temp
                        logger.debug('mean level => %s',section[sec_bounds].array.mean())

                    else:
                        # If we care about the correct abosrption depth as a function of
                        # wavelength, then do this differently.
                        # We need to do the accumulate step to get the right absorption depth,
                        # but that step will also apply tree rings and current pixel boundaries.
                        # So don't also do them above.  The photons here should just be uniform
                        # over the area of the current section.
                        nphotons = counts_per_iter * bounds.area()
                        # Poisson realization of the actual count
                        pd = galsim.PoissonDeviate(rng, mean=nphotons)
                        nphotons = int(pd())
                        photons = galsim.PhotonArray(nphotons)
                        # Generate random positions in area
                        ud = galsim.UniformDeviate(rng)
                        ud.generate(photons.x)
                        ud.generate(photons.y)
                        photons.x = photons.x * (bounds.xmax - bounds.xmin + 1) + bounds.xmin - 0.5
                        photons.y = photons.y * (bounds.ymax - bounds.ymin + 1) + bounds.ymin - 0.5
                        photons.flux = 1
                        # Apply wavelengths from sed
                        wavelength_sampler.applyTo(photons, rng=rng)
                        # Accumulate the photons on the image
                        sensor.accumulate(photons, section)
                        logger.debug('added %d photons: mean level => %s',
                                     len(photons), section[sec_bounds].array.mean())

                # Copy just the part that is officially part of this section.
                image[sec_bounds] += section[sec_bounds]
                logger.info('Done section: mean level => %s', image[sec_bounds].array.mean())

    def getNObj(self, config, base, image_num, logger=None):
        """Get the number of objects that will be built for this image.

        There are no objects drawn in a flat, so this returns 0.

        Parameters:
            config:     The configuration dict for the image field.
            base:       The base configuration dict.
            image_num:  The current image number.
            logger:     If given, a logger object to log progress.

        Returns:
            the number of objects (=0)
        """
        return 0

RegisterImageType('LSST_Flat', LSST_FlatBuilder())
